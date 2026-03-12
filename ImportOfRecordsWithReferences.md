# Streamlined Import of Records with References

> **Status: Implemented** -- This design has been implemented across the Registry and Document Store services. The sections below describe the actual working behavior.

## 1. Overview

This document describes the architectural design and implementation of streamlined import for WIP documents that contain references to other documents. The goal was to reduce the number of API calls required by the client, simplify the import logic, and better align the system's behavior with the core philosophy that all unique identifiers, including synonyms, are first-class citizens.

## 2. The Problem: The "Chatty" Import Workflow

The previous workflow for importing a record (e.g., an Invoice) that references another record (e.g., a Customer) via a synonym or business key was "chatty," requiring four distinct API calls and significant client-side orchestration.

### Previous Workflow Breakdown

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

This process required a total of **four API calls** and forced the client to manage the state and logic of this multi-step orchestration.

## 3. The Streamlined Workflow

The streamlined workflow reduces the process to just two API calls by shifting the responsibility of identity resolution to the server, where it belongs.

### Workflow

1.  **Create the Customer (with optional synonyms):** The client creates the customer and provides synonyms in the same API call.
    *   `POST /api/document-store/documents`
        ```json
        {
          "template_id": "TPL-XXXXXX",
          "data": { "name": "Acme Corp", "email": "acme@example.com" },
          "synonyms": [{ "external_id": "ext_123" }]
        }
        ```
2.  **Create the Invoice (referencing by synonym):** The client creates the invoice, referencing the customer directly by its synonym. The server resolves it automatically via the Registry.
    *   `POST /api/document-store/documents`
        ```json
        {
          "template_id": "TPL-YYYYYY",
          "data": { "invoice_id": "INV-456", "customer": "ext_123" }
        }
        ```

This process requires only **two API calls**, drastically simplifying client-side logic and reducing network latency.

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
1.  **Coupling is Justified:** The Document Store does not couple to a vague "search" function but to a well-defined "identity resolution" function, which is a core responsibility of the Registry. This is a legitimate and necessary interaction.
2.  **Performance is Addressed:** Because a synonym lookup is an indexed, O(1) operation, the performance impact is minimal and acceptable, as it saves the client a full network round-trip.
3.  **Ambiguity is Eliminated:** The guarantee of uniqueness within a namespace means the lookup returns one or zero results, removing the need for complex ambiguity handling in the Document Store.

## 5. Architectural Design

The streamlined workflow is implemented through the following enhancements.

### 1. Registry Service Enhancement

Rather than introducing a new endpoint, the existing `POST /api/registry/entries/lookup/by-id` endpoint was extended with a 3-step resolution cascade:

1.  **entry_id match** -- Direct lookup by the `entry_id` field.
2.  **additional_ids match** -- Search in the `additional_ids` array.
3.  **composite key value match** -- Search in the `search_values` flat array (all string values from primary + synonym composite keys).

*   **Endpoint:** `POST /api/registry/entries/lookup/by-id`
*   **Request format** (existing bulk model):
    ```json
    [
      { "entry_id": "ext_123", "namespace": "wip-documents" }
    ]
    ```
*   **The `namespace` field is optional.** When set to `null`, the lookup searches all namespaces, enabling cross-namespace resolution.
*   **Response** includes a `matched_via` field indicating how the entry was found: `"entry_id"`, `"additional_id"`, or `"composite_key_value"`.

### 2. Document Store Service Enhancement

The validation logic for `reference` fields within the Document Store performs server-side resolution using the following cascade.

**Resolution Cascade:**
When validating a field of `type: "reference"`, the service resolves the provided value in the following order:

1.  **UUID7 pattern** -- If the value matches a UUID7 format, perform a direct `document_id` lookup.
2.  **`hash:` prefix** -- If the value starts with `hash:`, perform an `identity_hash` lookup.
3.  **Registry lookup** -- Call `POST /api/registry/entries/lookup/by-id` to resolve the value. If found, fetch the document by the resolved ID. If that document is inactive, follow the `identity_hash` chain to find the latest active version.
4.  **String fallback** -- Treat the value as a business key and look up the document via its identity fields.
5.  **Dict** -- If the value is a dictionary, perform a composite business key lookup.

**Document creation** now accepts an optional `synonyms` field to register synonyms at creation time, eliminating the need for a separate API call to the Registry.

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
