# Streamlined Import of Records with References

## 1. Overview

This document summarizes the architectural discussion and resulting design for streamlining the import of WIP documents that contain references to other documents. The primary goal is to reduce the number of API calls required by the client, simplify the import logic, and better align the system's behavior with the core philosophy that all unique identifiers, including synonyms, are first-class citizens.

## 2. The Problem: The "Chatty" Import Workflow

The current, validated workflow for importing a record (e.g., an Invoice) that references another record (e.g., a Customer) via a synonym or business key is "chatty," requiring four distinct API calls and significant client-side orchestration.

### Current Workflow Breakdown

**Part 1: Create the Referenced Entity (Customer) with a Synonym**

1.  **Create the Customer:** The client sends a `POST` request to the Document Store to create the customer document.
    *   `POST /api/document-store/documents` -> returns `{ "document_id": "CUS-001" }`
2.  **Add the Synonym:** The client sends a second `POST` request to the Registry to associate a synonym (e.g., an external ID `ext_123`) with the newly created Customer's canonical ID.
    *   `POST /api/registry/synonyms/add` (with `target_id: "CUS-001"` and `synonym_composite_key: { "external_id": "ext_123" }`)

**Part 2: Create the Referencing Entity (Invoice)**

3.  **Resolve the Synonym:** The client must first look up the synonym to find the canonical WIP ID.
    *   `POST /api/registry/search/by-fields` (with `field_criteria: { "external_id": "ext_123" }`) -> returns `{ "registry_id": "CUS-001" }`
4.  **Create the Invoice:** The client uses the resolved canonical ID to create the invoice document.
    *   `POST /api/document-store/documents` (with invoice data including `"customer": "CUS-001"`)

This process requires a total of **four API calls** and forces the client to manage the state and logic of this multi-step orchestration.

## 3. The Goal: A Streamlined Workflow

The proposed, more efficient workflow would reduce the process to just two API calls by shifting the responsibility of identity resolution to the server, where it belongs.

### Proposed Workflow Breakdown

1.  **Create the Customer (with optional synonym):** The client creates the customer and provides the synonym in the same API call.
    *   `POST /api/document-store/documents` (with customer data and an optional `synonyms` field)
2.  **Create the Invoice (referencing by synonym):** The client creates the invoice, referencing the customer directly by its synonym.
    *   `POST /api/document-store/documents` (with invoice data including `"customer": "ext_123"`)

This process would require only **two API calls**, drastically simplifying client-side logic and reducing network latency.

## 4. Architectural Discussion & Justification

Initial objections to the streamlined workflow were raised based on standard microservice principles, but these were successfully refuted by a deeper understanding of WIP's core philosophy.

#### Initial Objections:
*   **Service Coupling:** Having the Document Store resolve synonyms would increase its coupling to the Registry's search functionality.
*   **Performance:** Introducing a blocking lookup call from the Document Store to the Registry would slow down the critical document validation path.
*   **Ambiguity:** If a synonym could resolve to multiple entities, the Document Store would be forced to handle ambiguity that the client should resolve.

#### The Decisive Philosophical Insight:
These objections were based on a misunderstanding of the role of synonyms in WIP. The correct and guiding principle is:

> **Synonyms are first-class citizens in WIP.** They are not random strings. They are guaranteed to be unique within their namespace, and synonym lookup should be as fast and reliable as a canonical WIP ID lookup, likely using the same underlying database indexes.

This principle reframes the problem and invalidates the initial objections:
1.  **Coupling is Justified:** The Document Store is not coupling to a vague "search" function but to a well-defined "identity resolution" function, which is a core responsibility of the Registry. This is a legitimate and necessary interaction.
2.  **Performance is Addressed:** If a synonym lookup is an indexed, O(1) operation, the performance impact is minimal and acceptable, as it saves the client a full network round-trip.
3.  **Ambiguity is Eliminated:** The guarantee of uniqueness within a namespace means the lookup will return one or zero results, removing the need for complex ambiguity handling in the Document Store.

## 5. Proposed Architectural Design

To implement the streamlined workflow, the following enhancements should be made.

### 1. Registry Service Enhancement

The Registry should expose a unified, high-performance endpoint for resolving any unique identifier—be it a canonical ID, a hashed composite key, or a synonym.

*   **Endpoint:** `POST /api/registry/resolve`
*   **Request:**
    ```json
    {
      "identifiers": [
        { "pool_id": "wip-documents", "value": "ext_123" },
        { "pool_id": "wip-documents", "value": "CUS-001" }
      ]
    }
    ```
*   **Behavior:** The endpoint would efficiently check against all indexed unique identifiers and return the canonical ID for each resolved value.

### 2. Document Store Service Enhancement

The validation logic for `reference` fields within the Document Store must be updated to perform server-side resolution.

**New Resolution Cascade:**
When validating a field of `type: "reference"`, the service will attempt to resolve the provided `lookup_value` in the following order:

1.  **Is it a Canonical ID?** Check if the value matches the format of a canonical WIP ID (e.g., a UUID7 for documents). If it exists and is valid, resolution succeeds.
2.  **Is it a Synonym/Business Key?** If not a canonical ID, call the Registry's new `resolve` endpoint to look it up as a synonym or other unique identifier.
3.  **Success or Failure:**
    *   If the Registry returns a canonical ID, resolution succeeds. The Document Store proceeds to validate that the resolved entity is of the correct template type (e.g., `CUSTOMER_SYNONYM_TEST`).
    *   If the Registry does not find a match, validation fails with a "Referenced document not found" error.

### 3. Client Impact

The client-side implementation becomes dramatically simpler.

**Before (Client must orchestrate):**
```json
// Client has to do this
const canonicalId = await registry.lookup("ext_123"); 

// Then create the invoice
await documentStore.create({
  template_id: "INVOICE_TEMPLATE",
  data: {
    invoice_id: "INV-456",
    customer: canonicalId 
  }
});
```

**After (Server handles resolution):**
```json
// Client simply provides the synonym
await documentStore.create({
  template_id: "INVOICE_TEMPLATE",
  data: {
    invoice_id: "INV-456",
    customer: "ext_123" // The synonym
  }
});
```

## 6. Benefits

*   **Reduced Latency:** Fewer network round-trips result in faster imports.
*   **Simplified Client Logic:** Clients no longer need to implement complex, multi-step orchestration for creating linked documents.
*   **Improved Developer Experience (DX):** The API becomes more intuitive and easier to work with.
*   **Better Alignment with Philosophy:** The system's behavior is brought in line with the core principle of treating all unique identifiers as first-class citizens.
*   **Increased Robustness:** Centralizing the resolution logic on the server ensures it is handled consistently and efficiently across all clients.
