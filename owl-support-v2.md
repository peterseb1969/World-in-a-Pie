# OWL v2: Advanced Ontology Support in WIP

**Version:** 2.0
**Status:** Design Document

## 1. Philosophy: An Assertion Store, Not a Reasoner

This document describes the advanced capabilities of World In a Pie (WIP) for modeling and storing data from OWL (Web Ontology Language) ontologies. It supersedes the original `owl-support.md` document, reflecting the transformative impact of the `reference` field type.

WIP's core philosophy remains unchanged: **it is a generic, domain-agnostic assertion store, not a reasoner or a triple store.** WIP's role is to provide a robust, validated, and versioned source of truth for ontological assertions (triples). The interpretation, inference, and complex querying of this data (e.g., via SPARQL) are the responsibility of downstream systems like graph databases or dedicated OWL reasoners.

The introduction of the `reference` field type is the key enabler for this "v2" support, elevating WIP's capabilities from providing clunky workarounds to offering powerful, native-like primitives for modeling knowledge graphs.

## 2. Core Concept: Mapping OWL to WIP Primitives

The power of WIP's OWL support comes from a direct and intuitive mapping of core OWL concepts to WIP's existing primitives.

| OWL Concept | WIP Primitive | Notes & Enhancements with v2 |
| :--- | :--- | :--- |
| **Class** (`owl:Class`) | **Terminology** | A `Terminology` represents a class, with its `Terms` representing subclasses or enumerated members. |
| **Individual** (`owl:Individual`) | **Document** | A `Document` is an instance of a class, conforming to a specific `Template`. This is a key v2 concept. |
| **Data Property** (`owl:DatatypeProperty`) | **Template Field** (`string`, `number`, `term`) | Links an individual (Document) to a literal value. This remains a core strength. |
| **Object Property** (`owl:ObjectProperty`) | **`reference` Field** (`reference_type: document`) | **The cornerstone of v2 support.** Creates a direct, validated, and navigable link between two individuals (Documents). |
| **Domain** (`rdfs:domain`) | **The Template** | The domain of a property is implicitly defined by the Template that contains the property (field) definition. |
| **Range** (`rdfs:range`) | **`target_templates` Property** | The range of an object property is explicitly enforced by the `target_templates` array in a `reference` field. |
| **Subclass** (`rdfs:subClassOf`) | **Term Hierarchy** (`parent_term_id`) | Hierarchies of classes can be modeled within a Terminology using parent-child relationships between terms. |
| **Equivalence** (`owl:sameAs`) | **Registry Synonyms** | The Registry's synonym feature provides a powerful mechanism for linking equivalent individuals or concepts across different vocabularies. |

## 3. Detailed Implementation with Examples

### 3.1. Modeling Classes and Individuals

-   An **OWL Class** is represented by a WIP **Terminology**.
-   An **OWL Individual** is represented by a WIP **Document**.

First, we define our classes as `Terminologies`. The `Terms` within can represent specific, enumerable individuals or subclasses.

```json
// POST /api/def-store/terminologies
{
  "code": "DISEASE",
  "name": "Disease"
}
```

Next, we create a `Template` that defines the structure for individuals of this class.

```json
// POST /api/template-store/templates
{
  "code": "DISEASE_INDIVIDUAL",
  "name": "Disease Individual",
  "identity_fields": ["disease_code"],
  "fields": [
    { "name": "disease_code", "label": "Disease Code", "type": "string", "mandatory": true },
    { "name": "label", "label": "Label", "type": "string", "mandatory": true },
    { "name": "description", "label": "Description", "type": "string" }
  ]
}
```

Now, creating a `Document` based on this `Template` is equivalent to creating an `owl:Individual`.

### 3.2. Modeling Properties

#### Object Properties (`owl:ObjectProperty`)

This is where the `reference` field type is transformative. An object property is a relationship between two individuals, which maps perfectly to a `reference` field linking two `Documents`.

Let's model the property `affects`, which has a domain of `DISEASE_FINDING` and a range of `ORGAN`.

**Step 1: Define the `ORGAN` template for our range.**
```json
{
  "code": "ORGAN",
  "name": "Organ",
  "identity_fields": ["organ_name"],
  "fields": [
    { "name": "organ_name", "label": "Organ Name", "type": "string", "mandatory": true }
  ]
}
```

**Step 2: Define the `DISEASE_FINDING` template with the `affects` property.**
```json
{
  "code": "DISEASE_FINDING",
  "name": "Disease Finding",
  "identity_fields": ["finding_id"],
  "fields": [
    { "name": "finding_id", "label": "Finding ID", "type": "string", "mandatory": true },
    {
      "name": "subject_disease",
      "label": "Subject Disease",
      "type": "reference",
      "reference_type": "document",
      "target_templates": ["DISEASE_INDIVIDUAL"] 
    },
    {
      "name": "affects",
      "label": "Affects",
      "type": "reference",
      "reference_type": "document",
      "target_templates": ["ORGAN"],
      "description": "Implements the 'affects' object property with a range of ORGAN."
    }
  ]
}
```
Here, `target_templates: ["ORGAN"]` is the explicit enforcement of `rdfs:range`, and the fact that this field lives in the `DISEASE_FINDING` template implicitly defines the `rdfs:domain`.

#### Data Properties (`owl:DatatypeProperty`)

A data property links an individual to a literal value. This is modeled using standard WIP field types like `string`, `number`, or `term`.

```json
// Continuing the DISEASE_FINDING template
"fields": [
  // ... (previous fields)
  {
    "name": "severity",
    "label": "Severity",
    "type": "term",
    "terminology_ref": "SEVERITY_LEVELS",
    "description": "Implements the 'hasSeverity' data property."
  },
  {
    "name": "patient_age",
    "label": "Patient Age",
    "type": "integer",
    "description": "Implements the 'hasAge' data property."
  }
]
```

### 3.3. Creating Assertions (Triples)

A WIP `Document` is an assertion that creates a set of triples.

First, create the individuals (documents) we need:
```json
// Create an Organ individual
// POST /api/document-store/documents
{
  "template_id": "ORGAN_TEMPLATE_ID",
  "data": { "organ_name": "Pancreas" }
} 
// Returns document_id: "ORGAN-001"

// Create a Disease individual
// POST /api/document-store/documents
{
  "template_id": "DISEASE_INDIVIDUAL_TEMPLATE_ID",
  "data": { "disease_code": "E10", "label": "Type 1 Diabetes" }
}
// Returns document_id: "DISEASE-001"
```

Now, create the assertion using the `DISEASE_FINDING` template:
```json
// POST /api/document-store/documents
{
  "template_id": "DISEASE_FINDING_TEMPLATE_ID",
  "data": {
    "finding_id": "FINDING-XYZ",
    "subject_disease": "DISEASE-001", // Reference by canonical ID
    "affects": "Pancreas",            // Reference by business key
    "severity": "Moderate"
  }
}
```
This single document represents multiple triples that a downstream system can materialize:
- `(FINDING-XYZ, has_subject_disease, DISEASE-001)`
- `(FINDING-XYZ, affects, ORGAN-001)`
- `(FINDING-XYZ, has_severity, "Moderate")`

## 4. Importing an OWL Ontology

WIP does not have a one-click OWL import feature. Instead, it provides the APIs to enable a scripted import process. This gives the user full control over how the ontology is mapped to WIP primitives.

A typical import script (e.g., in Python using the `rdflib` library) would follow these steps:

**Step 1: Parse the Ontology File**
The script reads an `.owl`, `.ttl`, or `rdf/xml` file into an in-memory graph.

```python
import rdflib
g = rdflib.Graph()
g.parse("path/to/your/ontology.owl")
```

**Step 2: Create Terminologies and Terms for Classes**
Iterate through all `owl:Class` and `rdfs:subClassOf` statements to build WIP `Terminologies` and hierarchical `Terms`.

**Step 3: Create Templates for Properties**
For each `owl:ObjectProperty` and `owl:DatatypeProperty`, create a corresponding WIP `Template`. The `rdfs:domain` and `rdfs:range` from the ontology can be used to configure the template fields and `target_templates`.

**Step 4: Create Documents for Individuals**
Iterate through all `owl:Individual` definitions and create a corresponding WIP `Document` for each one, using the appropriate `Template`.

**Step 5: Create Documents for Assertions**
Iterate through all property assertions in the graph and create WIP `Documents` that represent these triples, using the templates created in Step 3.

## 5. A Dedicated OWL Namespace

To maintain a clean separation between formal, imported ontologies and simpler, local terminologies (like a list of US states), it is highly recommended to create a dedicated **namespace group** for each major ontology.

### Purpose
-   **Prevents Collisions:** Ensures that a term `T-001` from your imported SNOMED ontology does not collide with `T-001` from a local "T-Shirt Sizes" terminology.
-   **Isolation:** Allows you to manage, query, back up, and archive an entire ontology as a single unit.
-   **Clarity:** Makes it clear which data belongs to a formal ontology versus local, application-specific vocabularies.

### Implementation
Use the Registry API to create a new namespace group before starting the import.

```json
// POST /api/registry/namespaces
{
  "prefix": "snomed",
  "description": "Namespace for the imported SNOMED CT ontology"
}
```

This will automatically create a set of isolated ID pools:
-   `snomed-terminologies`
-   `snomed-terms`
-   `snomed-templates`
-   `snomed-documents`
-   `snomed-files`

The import script would then use these specific namespaces when creating all entities.

## 6. What WIP is NOT

To reiterate, WIP's role is well-defined. By providing these powerful primitives, it serves as an excellent foundation for a knowledge graph, but it is not one by itself. WIP is **NOT**:

-   **A Reasoner:** It performs no logical inference. It will not infer that if "Diabetes affects Pancreas" and "Pancreas is part of Endocrine System," that "Diabetes affects Endocrine System."
-   **A Triple Store:** It stores structured JSON documents, not raw triples. Triples are materialized from these documents by downstream systems.
-   **A SPARQL Endpoint:** Querying is done via the REST API or by connecting a BI tool to the PostgreSQL reporting layer.

WIP is the source of truth for the **explicit assertions** that a downstream reasoner or graph database can then use to build, infer, and query a complete knowledge graph.

![OWL to WIP Flow](https://i.imgur.com/example.png)
*(Note: A diagram would be placed here illustrating the flow from OWL concepts to WIP primitives and then to downstream consumers.)*
