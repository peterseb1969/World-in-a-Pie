# OWL Ontology Support in WIP

This document outlines how WIP can support OWL (Web Ontology Language) ontologies using its existing primitives, without building a separate ontology management system.

## Philosophy

WIP's core philosophy applies to OWL support:

1. **Generic first** - Model the primitives, not specific use cases
2. **Validation is the core value** - WIP ensures data conforms to templates
3. **Meaning is downstream** - WIP stores structured data; interpretation happens in consumers
4. **Don't store what you can derive** - Triples can be materialized from documents

**WIP does not need to understand OWL.** It provides the building blocks; downstream systems (graph databases, reasoners, OWL converters) interpret the semantics.

## How OWL Maps to WIP Primitives

| OWL Concept | WIP Primitive | Notes |
|-------------|---------------|-------|
| **Class** | Terminology + Terms | Each terminology is a class hierarchy |
| **Individual** | Term | A term is an instance of a class |
| **owl:sameAs** | Registry Synonyms | Terms from different terminologies linked as synonyms |
| **Object Property** | Template field (term type) | Relationship defined by template schema |
| **Property Instance** | Document | A document with term references captures relationships |
| **rdfs:subClassOf** | Term parent/child | Already supported in Def-Store |

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         OWL via WIP Primitives                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  TERMINOLOGIES = OWL Classes                                                 │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐              │
│  │ SNOMED          │  │ ICD10           │  │ LOCAL_CONCEPTS  │              │
│  │ Terms:          │  │ Terms:          │  │ Terms:          │              │
│  │ - T-001 Diabetes│  │ - T-100 E11     │  │ - T-200 MyTerm  │              │
│  │ - T-002 Type1   │  │ - T-101 E10     │  │                 │              │
│  └────────┬────────┘  └────────┬────────┘  └─────────────────┘              │
│           │                    │                                             │
│           └───────┬────────────┘                                             │
│                   ▼                                                          │
│  REGISTRY = owl:sameAs                                                       │
│  ┌─────────────────────────────────────────┐                                │
│  │ T-001 (SNOMED:Diabetes) ←synonym→       │                                │
│  │ T-100 (ICD10:E11)                       │                                │
│  │                                         │                                │
│  │ Query: "synonyms of T-001"              │                                │
│  │ Returns: [T-001, T-100] + terminology   │                                │
│  └─────────────────────────────────────────┘                                │
│                                                                              │
│  TEMPLATES = Relationship Schemas                                            │
│  ┌─────────────────────────────────────────┐                                │
│  │ CLINICAL_FINDING                        │                                │
│  │ Fields:                                 │                                │
│  │   - condition: term (any)     ──────────┼──► "subject"                   │
│  │   - affects: term (ANATOMY)   ──────────┼──► "predicate + object"        │
│  │   - severity: term (SEVERITY) ──────────┼──► "data property"             │
│  └─────────────────────────────────────────┘                                │
│                                                                              │
│  DOCUMENTS = Relationship Instances                                          │
│  ┌─────────────────────────────────────────┐                                │
│  │ {                                       │                                │
│  │   condition: "Diabetes" (T-001),        │                                │
│  │   affects: "Pancreas" (T-042),          │                                │
│  │   severity: "Moderate" (T-100)          │                                │
│  │ }                                       │                                │
│  └─────────────────────────────────────────┘                                │
│                   │                                                          │
│                   ▼                                                          │
│  DOWNSTREAM = Triple Materialization                                         │
│  ┌─────────────────────────────────────────┐                                │
│  │ Stream subscriber / PostgreSQL / View   │                                │
│  │                                         │                                │
│  │ Derived triples:                        │                                │
│  │   (snomed:Diabetes, affects, Pancreas)  │                                │
│  │   (snomed:Diabetes, severity, Moderate) │                                │
│  │                                         │                                │
│  │ Export to: Neo4j, GraphDB, Turtle, etc  │                                │
│  └─────────────────────────────────────────┘                                │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Key Capabilities (Already Implemented)

### 1. Term Identity Across Terminologies (Registry Synonyms)

The Registry already supports synonyms:

```bash
# Register a term with synonyms
POST /api/registry/namespaces/wip-terms/keys
{
  "composite_key": {"code": "Diabetes", "terminology": "SNOMED"},
  "synonyms": ["ICD10:E11", "LOCAL:diabetes-001"]
}

# Query synonyms
GET /api/registry/namespaces/wip-terms/keys/{key_id}/synonyms
# Returns all equivalent term IDs
```

This provides `owl:sameAs` functionality without special OWL infrastructure.

### 2. Term Hierarchies (Def-Store Parent/Child)

Terms already support parent-child relationships:

```json
{
  "term_id": "T-002",
  "code": "TYPE1_DIABETES",
  "value": "Type 1 Diabetes",
  "parent_term_id": "T-001"  // T-001 = Diabetes
}
```

This provides `rdfs:subClassOf` for terms within a terminology.

### 3. Relationship Schemas (Templates)

Templates define what relationships can exist:

```json
{
  "code": "DISEASE_AFFECTS_ORGAN",
  "fields": [
    {"name": "disease", "type": "term", "terminology_ref": "DISEASES"},
    {"name": "organ", "type": "term", "terminology_ref": "ANATOMY"},
    {"name": "mechanism", "type": "term", "terminology_ref": "MECHANISMS"}
  ],
  "identity_fields": ["disease", "organ"]
}
```

### 4. Relationship Instances (Documents)

Documents capture actual relationships:

```json
{
  "template_id": "TPL-000042",
  "data": {
    "disease": "Type 1 Diabetes",
    "organ": "Pancreas",
    "mechanism": "Autoimmune destruction"
  },
  "term_references": {
    "disease": "T-002",
    "organ": "T-100",
    "mechanism": "T-200"
  }
}
```

### 5. Streaming to Downstream (NATS)

Documents are published to NATS on create/update:

```
Subject: wip.documents.created
Payload: {full document with term_references}
```

Downstream subscribers can:
- Materialize triples to PostgreSQL
- Forward to graph databases (Neo4j, Amazon Neptune)
- Export to OWL formats (Turtle, RDF/XML)

### 6. Term-to-Terminology Lookup (Implemented)

Term responses now include `terminology_code` alongside `terminology_id`:

```json
// GET /api/def-store/terms/{term_id}
{
  "term_id": "T-001",
  "terminology_id": "TERM-000042",
  "terminology_code": "SNOMED",
  "code": "73211009",
  "value": "Diabetes mellitus",
  // ... other fields
}
```

This allows downstream systems to construct proper OWL URIs like `snomed:73211009`.

For synonym queries, downstream can fetch term details to get the terminology reference:

```bash
# Get synonyms from Registry
GET /api/registry/namespaces/wip-terms/keys/{key_id}/synonyms
# Returns: [T-001, T-100]

# Fetch term details to get terminology
GET /api/def-store/terms/T-001
# Returns: {terminology_code: "SNOMED", ...}

GET /api/def-store/terms/T-100
# Returns: {terminology_code: "ICD10", ...}
```

## What WIP Does NOT Do

| Capability | WIP's Role | Where It Happens |
|------------|-----------|------------------|
| **Reasoning** | Stores assertions | External reasoner (Pellet, HermiT) |
| **Inference** | Stores explicit triples | Computed downstream |
| **Consistency checking** | Validates against templates | OWL reasoner for semantic consistency |
| **SPARQL queries** | Provides data via API/stream | Graph DB or triple store |
| **OWL serialization** | Stores in MongoDB/PostgreSQL | Export adapter converts to Turtle/RDF |

## Potential Future Enhancements

### 1. OWL Predefined Terminologies

Ship standard terminologies for OWL relationships:

```
Terminology: OWL_OBJECT_PROPERTIES
Terms:
  - subClassOf
  - equivalentClass
  - disjointWith
  - partOf
  - hasPart
  - ...

Terminology: OWL_DATA_PROPERTIES
Terms:
  - hasLabel
  - hasDescription
  - hasVersion
  - ...
```

### 2. OWL Predefined Templates

Ship templates for common OWL patterns:

```json
{
  "code": "OWL_CLASS_ASSERTION",
  "fields": [
    {"name": "subject", "type": "term"},
    {"name": "predicate", "type": "term", "terminology_ref": "OWL_OBJECT_PROPERTIES"},
    {"name": "object", "type": "term"}
  ]
}
```

### 3. OWL Validation Mode

Optional strict validation for OWL semantics:

- Enforce that `subClassOf` targets exist
- Validate cardinality restrictions
- Check domain/range constraints

### 4. OWL Export Adapter

Stream subscriber that exports to OWL formats:

```
NATS subscription: wip.documents.>
Output: Turtle files, or direct insert to triple store
```

## Summary

WIP can support OWL ontologies by:

1. **Using terminologies as classes** - Implemented
2. **Using Registry synonyms for owl:sameAs** - Implemented
3. **Using term hierarchies for rdfs:subClassOf** - Implemented
4. **Using templates for relationship schemas** - Implemented
5. **Using documents for relationship instances** - Implemented
6. **Term responses include terminology reference** - Implemented
7. **Leaving reasoning to downstream** - Follows WIP philosophy

All necessary primitives for OWL support are in place. Downstream systems (graph databases, reasoners, OWL exporters) can use WIP's APIs to build full ontology workflows.
