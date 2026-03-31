# @wip/client

TypeScript client library for WIP (World In a Pie) services. Framework-agnostic, zero runtime dependencies, uses native `fetch`. Works in browsers and Node.js 18+.

## Critical Behaviors

Before writing any code, understand these non-obvious behaviors that cause the most confusion:

### Bulk-First API: HTTP 200 Does Not Mean Success

Every WIP write endpoint is bulk. It accepts an array and **always returns HTTP 200** — even when items fail. Errors are per-item inside `results[]`.

```typescript
const response = await client.defStore.createTerminologies([
  { value: 'VALID', label: 'Valid' },
  { value: 'DUPLICATE', label: 'Duplicate' },  // already exists
])

// response.results[0].status === "created"  ✓
// response.results[1].status === "error"    ✗  but HTTP was still 200!
// response.succeeded === 1, response.failed === 1
```

**Single-item convenience methods** (e.g., `createTerminology()`) wrap the bulk API and **throw `WipBulkItemError`** if the item fails. Bulk methods (e.g., `createTerminologies()`, `createTerms()`, `createDocuments()`) return `BulkResponse` — you must check `results[i].status` yourself.

### Document Identity: Same Data = Same ID, New Version

Templates define `identity_fields` (e.g., `["email"]`). When you create a document, the identity fields are hashed (SHA-256) and sent to the Registry. If the hash matches an existing document, you get the **same `document_id`** with an incremented `version` — this is an upsert, not a duplicate.

```typescript
// First call: creates DOC-001 version 1
await client.documents.createDocument({
  template_id: 'TPL-PATIENT',
  data: { email: 'jane@example.com', name: 'Jane' },
})

// Second call with same email: creates DOC-001 version 2 (NOT a new document)
await client.documents.createDocument({
  template_id: 'TPL-PATIENT',
  data: { email: 'jane@example.com', name: 'Jane Doe' },  // name changed
})
```

**Zero identity fields** = every submission creates a new document (append-only, no update path). **Too many identity fields** = corrections create duplicates instead of new versions. Never include timestamps or per-run data in identity fields.

### Template Versioning: Multiple Active Versions

When you update a template, the `template_id` stays the same but `version` increments. **Both versions remain active simultaneously.** Documents pin to a specific `(template_id, version)`.

```typescript
// Get latest version (default)
const latest = await client.templates.getTemplate('TPL-001')

// Get specific version
const v1 = await client.templates.getTemplate('TPL-001', 1)

// List documents for a specific template version
const docs = await client.documents.listDocuments({
  template_id: 'TPL-001',
  template_version: 1,
})
```

### Soft Delete: Nothing Is Really Deleted

All delete operations set `status: "inactive"` — records remain in the database. The only exception is `files.hardDeleteFile()` which permanently removes the file from MinIO storage.

### Retry Behavior

GET requests retry automatically on 502/503/504 with exponential backoff (default: 2 retries). **POST/PUT/DELETE never retry** — mutations fail immediately on any error. This prevents double-creates.

---

## Quick Start

```typescript
import { createWipClient } from '@wip/client'

const client = createWipClient({
  baseUrl: '/wip',  // In browser: resolved to window.location.origin + '/wip'
  auth: { type: 'api-key', key: 'dev_master_key_for_testing' },
})

// List terminologies
const terminologies = await client.defStore.listTerminologies({ status: 'active' })

// Create a terminology (single-item convenience — throws on error)
const result = await client.defStore.createTerminology({
  value: 'GENDER',
  label: 'Gender',
})
console.log(result.id) // UUID7

// Bulk create terms (returns BulkResponse — check results[] yourself)
const bulkResult = await client.defStore.createTerms('T-001', [
  { value: 'MALE', label: 'Male' },
  { value: 'FEMALE', label: 'Female' },
])
console.log(bulkResult.succeeded) // 2
```

## Installation

Local package — reference via `file:` or tgz in consumer's `package.json`:

```json
{
  "dependencies": {
    "@wip/client": "file:../../libs/wip-client"
  }
}
```

Zero runtime dependencies. Requires Node.js 18+ (for native `fetch` and `FormData`). Works in all modern browsers.

## Configuration

```typescript
import { createWipClient, ApiKeyAuthProvider, OidcAuthProvider } from '@wip/client'

const client = createWipClient({
  // Required: base URL of your WIP instance
  // In browser behind Vite proxy: '/wip' (resolved to origin + '/wip')
  // In browser direct to Caddy: '' (resolved to window.location.origin)
  // For Node.js scripts: 'https://localhost:8443' or 'https://your-host:8443'
  baseUrl: '/wip',

  // Auth: API key, OIDC token callback, or custom AuthProvider
  auth: { type: 'api-key', key: 'your-key' },
  // auth: { type: 'oidc', getToken: () => myOidcLib.getAccessToken() },

  // Optional: request timeout in ms (default: 30000)
  timeout: 30_000,

  // Optional: retry config for GET requests (default: 2 retries with exponential backoff)
  retry: { maxRetries: 2, baseDelayMs: 500, maxDelayMs: 5_000 },

  // Optional: called on 401/403 responses (e.g., redirect to login)
  onAuthError: () => { window.location.href = '/login' },
})

// Switch auth at runtime (e.g., after login)
client.setAuth(new ApiKeyAuthProvider('new-key'))
client.setAuth(new OidcAuthProvider(() => oidcManager.getAccessToken()))
```

### Auth Providers

| Provider | Header | Use Case |
|----------|--------|----------|
| `ApiKeyAuthProvider(key)` | `X-API-Key: <key>` | Server-side scripts, dev/testing |
| `OidcAuthProvider(getToken)` | `Authorization: Bearer <token>` | Browser apps with OIDC |

The `OidcAuthProvider` takes a callback, not a static token. It calls your function on every request, so token refresh is handled by your OIDC library. The provider has **zero OIDC dependencies** — bring your own library (oidc-client-ts, Auth0, etc.).

API key auth headers are cached internally for performance. OIDC headers call the callback fresh each time.

---

## Services

| Service | Property | Port | Description |
|---------|----------|------|-------------|
| Def-Store | `client.defStore` | 8002 | Terminologies, terms, validation, ontology, import/export |
| Template-Store | `client.templates` | 8003 | Document schemas, versioning, draft mode, inheritance |
| Document-Store | `client.documents` | 8004 | Documents, versions, table view, CSV export, query |
| File-Store | `client.files` | 8004 | File upload/download (MinIO), orphan detection, integrity |
| Registry | `client.registry` | 8001 | Namespaces, ID management, synonyms, merge |
| Reporting-Sync | `client.reporting` | 8005 | Cross-service search, integrity checks, activity feed |

All services route through the `baseUrl`. The Caddy reverse proxy routes `/api/def-store/*`, `/api/template-store/*`, etc. to the correct service port. The client always sends requests to `baseUrl + /api/<service>/...` — Caddy is required to route them correctly. In browser apps behind a Vite proxy, use `baseUrl: '/wip'` (resolved to `window.location.origin + '/wip'`). Use `baseUrl: ''` for direct Caddy access. In Node.js scripts, use `baseUrl: 'https://your-host:8443'`.

---

## Def-Store (Terminologies & Terms)

### Terminologies

```typescript
// List with filtering and pagination
const list = await client.defStore.listTerminologies({
  status: 'active',
  value: 'COUNTRY',         // exact match on terminology code
  namespace: 'wip',
  page: 1,
  page_size: 50,            // default 50, max 100
})
// list.items: Terminology[], list.total, list.pages

// Get by ID
const terminology = await client.defStore.getTerminology('T-001')

// Create (single — throws on error)
const result = await client.defStore.createTerminology({
  value: 'COUNTRY',
  label: 'Country',
  description: 'ISO 3166 countries',
})
// result: { index: 0, status: "created", id: "...", value: "COUNTRY" }

// Bulk create (returns BulkResponse)
const bulk = await client.defStore.createTerminologies([
  { value: 'GENDER', label: 'Gender' },
  { value: 'SALUTATION', label: 'Salutation' },
])

// Update
await client.defStore.updateTerminology('T-001', { label: 'Updated Label' })

// Delete (soft-delete — sets status to inactive)
await client.defStore.deleteTerminology('T-001')
```

### Terms

```typescript
// List terms under a terminology
const terms = await client.defStore.listTerms('T-001', {
  status: 'active',
  search: 'united',          // substring search in value/label/aliases
  page: 1,
  page_size: 50,
})

// Get single term
const term = await client.defStore.getTerm('TERM-001')

// Create single term (throws on error)
await client.defStore.createTerm('T-001', {
  value: 'US',
  label: 'United States',
  aliases: ['USA', 'U.S.A.'],
})

// Bulk create terms with tuning options
const bulk = await client.defStore.createTerms('T-001', terms, {
  batch_size: 1000,             // items per batch (default: server-side)
  registry_batch_size: 50,      // registry calls per sub-batch
})

// Deprecate (with replacement pointer)
await client.defStore.deprecateTerm('TERM-001', {
  reason: 'Merged with TERM-002',
  replaced_by_term_id: 'TERM-002',
})

// Delete (soft-delete)
await client.defStore.deleteTerm('TERM-001')

// Update
await client.defStore.updateTerm('TERM-001', {
  label: 'Updated Label',
  aliases: ['new-alias'],
})
```

### Validation

```typescript
// Validate a single value against a terminology
const result = await client.defStore.validateValue({
  terminology_value: 'COUNTRY',
  value: 'US',
})

// Bulk validate
const results = await client.defStore.bulkValidate({
  items: [
    { terminology_value: 'COUNTRY', value: 'US' },
    { terminology_value: 'GENDER', value: 'Male' },
  ],
})
```

### Import/Export

```typescript
// Import a complete terminology with terms
const imported = await client.defStore.importTerminology({
  terminology: { value: 'ICD10', label: 'ICD-10 Codes' },
  terms: [
    { value: 'A00', label: 'Cholera' },
    { value: 'A01', label: 'Typhoid fever' },
  ],
})
// imported.terminology, imported.terms_result (BulkResponse)

// Export
const exported = await client.defStore.exportTerminology('T-001', {
  format: 'json',              // or 'csv'
  includeRelationships: true,
  includeInactive: false,
  includeMetadata: true,
})

// Import OBO Graph JSON (ontology files like GO, HPO, MONDO)
const ontology = await client.defStore.importOntology(oboGraphJson, {
  terminology_value: 'HPO',
  terminology_label: 'Human Phenotype Ontology',
  prefix_filter: 'HP:',
  batch_size: 1000,
  registry_batch_size: 50,
})
// ontology.terms.created, ontology.relationships.created, ontology.elapsed_seconds
```

### Ontology Relationships

```typescript
// List relationships for a term
const rels = await client.defStore.listRelationships({
  term_id: 'TERM-001',
  direction: 'outgoing',        // 'incoming', 'outgoing', or 'both'
  relationship_type: 'is_a',
})

// List all relationships across all terminologies
const allRels = await client.defStore.listAllRelationships({
  relationship_type: 'is_a',
  status: 'active',
  page: 1,
  page_size: 50,
})

// Create relationships
await client.defStore.createRelationships([
  { source_term_id: 'TERM-002', target_term_id: 'TERM-001', relationship_type: 'is_a' },
])

// Traversal
const ancestors = await client.defStore.getAncestors('TERM-001', {
  relationship_type: 'is_a',
  max_depth: 10,
})
const descendants = await client.defStore.getDescendants('TERM-001', { max_depth: 3 })
const parents = await client.defStore.getParents('TERM-001')
const children = await client.defStore.getChildren('TERM-001')
```

---

## Template-Store (Document Schemas)

```typescript
// List templates
const templates = await client.templates.listTemplates({
  latest_only: true,            // only latest version of each template
  status: 'active',
  extends: 'TPL-BASE',         // filter by parent template
  namespace: 'wip',
  page: 1,
  page_size: 50,
})

// Get template (latest version by default)
const template = await client.templates.getTemplate('TPL-001')
const v1 = await client.templates.getTemplate('TPL-001', 1)  // specific version

// Get by value (code name)
const byValue = await client.templates.getTemplateByValue('PATIENT_RECORD')

// Get all versions of a template
const versions = await client.templates.getTemplateVersions('PATIENT_RECORD')

// Get specific version by value
const v2 = await client.templates.getTemplateByValueAndVersion('PATIENT_RECORD', 2)

// Raw variants (without resolving inheritance)
const raw = await client.templates.getTemplateRaw('TPL-001')
const rawByValue = await client.templates.getTemplateByValueRaw('PATIENT_RECORD')

// Create a template
await client.templates.createTemplate({
  value: 'LAB_RESULT',
  label: 'Lab Result',
  identity_fields: ['patient_email', 'test_date'],
  fields: [
    { name: 'patient_email', label: 'Patient Email', type: 'string',
      mandatory: true, semantic_type: 'email', metadata: {} },
    { name: 'test_date', label: 'Test Date', type: 'date',
      mandatory: true, metadata: {} },
    { name: 'result', label: 'Result', type: 'number',
      mandatory: false, metadata: {} },
    { name: 'status', label: 'Status', type: 'term',
      mandatory: true, terminology_ref: 'LAB_STATUS', metadata: {} },
  ],
})

// Update (creates a new version — old version stays active)
await client.templates.updateTemplate('TPL-001', {
  label: 'Lab Result v2',
  fields: [/* updated fields */],
})

// Delete (soft-delete)
await client.templates.deleteTemplate('TPL-001', {
  version: 2,                   // delete specific version
  force: true,                  // bypass referential integrity check
})

// Validate data against a template
const validation = await client.templates.validateTemplate('TPL-001', {
  data: { patient_email: 'test@example.com' },
})

// Template inheritance
const children = await client.templates.getChildren('TPL-001')
const allDescendants = await client.templates.getDescendants('TPL-001')

// Draft mode: create without validating references, then activate
await client.templates.createTemplate({
  value: 'DRAFT_TPL',
  label: 'Draft',
  status: 'draft',              // skip reference validation
  fields: [/* ... */],
})
await client.templates.activateTemplate('TPL-DRAFT', { dry_run: true })  // preview
await client.templates.activateTemplate('TPL-DRAFT')                     // activate
```

### Field Types

| Type | Description | Special Properties |
|------|-------------|-------------------|
| `string` | Text value | `validation.pattern`, `validation.min_length`, `validation.max_length` |
| `number` | Decimal number | `validation.minimum`, `validation.maximum` |
| `integer` | Whole number | `validation.minimum`, `validation.maximum` |
| `boolean` | True/false | |
| `date` | ISO date (YYYY-MM-DD) | |
| `datetime` | ISO datetime | |
| `term` | Reference to a terminology term | `terminology_ref` (terminology value code) |
| `reference` | Reference to another document | `reference_type`, `target_templates` |
| `file` | Binary file reference | `file_config` (allowed types, max size, multiple) |
| `object` | Nested object | Child `fields` in template |
| `array` | Array of values | `array_item_type`, `array_terminology_ref` |

### Semantic Types

Fields can have `semantic_type` for enhanced validation and UI hints: `email`, `url`, `latitude`, `longitude`, `percentage`, `duration`, `geo_point`.

---

## Document-Store (Documents)

```typescript
// List with filtering
const docs = await client.documents.listDocuments({
  template_id: 'TPL-001',
  template_value: 'PATIENT_RECORD',
  status: 'active',
  page: 1,
  page_size: 50,
})

// Get document (latest version by default)
const doc = await client.documents.getDocument('DOC-001')
const v1 = await client.documents.getVersion('DOC-001', 1)  // specific version
const latest = await client.documents.getLatestDocument('DOC-001')

// Create document (upsert via identity hash)
const result = await client.documents.createDocument({
  template_id: 'TPL-001',
  data: { name: 'Jane Doe', email: 'jane@example.com' },
})
// result: { status: "created", id: "DOC-...", version: 1,
//           identity_hash: "abc123...", is_new: true }

// Bulk create (returns BulkResponse)
const bulk = await client.documents.createDocuments([
  { template_id: 'TPL-001', data: { name: 'Alice', email: 'alice@example.com' } },
  { template_id: 'TPL-001', data: { name: 'Bob', email: 'bob@example.com' } },
])
// bulk.results[0].status, bulk.succeeded, bulk.failed

// Document versions
const versions = await client.documents.getVersions('DOC-001')

// Look up by identity hash
const byIdentity = await client.documents.getDocumentByIdentity('abc123hash')

// Delete (soft-delete) and archive
await client.documents.deleteDocument('DOC-001', 'user@example.com')
await client.documents.archiveDocument('DOC-001', 'user@example.com')

// Validate without creating
const validation = await client.documents.validateDocument({
  template_id: 'TPL-001',
  data: { email: 'invalid' },
})
```

### Querying Documents

```typescript
// Simple query with filters
const results = await client.documents.queryDocuments({
  template_id: 'TPL-001',
  filters: [
    { field: 'data.country', operator: 'eq', value: 'US' },
    { field: 'data.age', operator: 'gte', value: 18 },
    { field: 'data.tags', operator: 'in', value: ['urgent', 'review'] },
    { field: 'data.notes', operator: 'exists', value: true },
    { field: 'data.email', operator: 'regex', value: '@example\\.com$' },
  ],
  sort_by: 'created_at',
  sort_order: 'desc',
  page: 1,
  page_size: 50,
})
```

**Available filter operators:** `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `in`, `nin`, `exists`, `regex`.

### Table View & CSV Export

```typescript
// Table view (denormalized, spreadsheet-like)
const table = await client.documents.getTableView('TPL-001', {
  status: 'active',
  page: 1,
  page_size: 100,
})
// table.columns: [{ name, label, type, is_array, is_flattened }]
// table.rows: Record<string, unknown>[]
// table.total_documents, table.total_rows

// Export as CSV (returns Blob)
const csv = await client.documents.exportTableCsv('TPL-001', {
  status: 'active',
  include_metadata: true,
})
```

---

## File-Store (Binary Files)

Files are stored in MinIO (S3-compatible) and tracked in MongoDB. The File-Store is part of the Document-Store service (same port 8004).

```typescript
// Upload a file (accepts File or Blob)
const file = await client.files.uploadFile(myFile, 'report.pdf', {
  description: 'Monthly report',
  tags: ['report', 'monthly'],
  category: 'reports',
  allowed_templates: ['TPL-REPORT'],  // restrict which templates can reference this file
})
// file: FileEntity { file_id, filename, content_type, size_bytes, checksum, ... }

// List files
const files = await client.files.listFiles({
  status: 'active',
  content_type: 'application/pdf',
  page: 1,
  page_size: 50,
})

// Get file metadata
const meta = await client.files.getFile('FILE-001')

// Download — two options
const { download_url } = await client.files.getDownloadUrl('FILE-001', 3600)  // URL valid for 1 hour
const blob = await client.files.downloadFileContent('FILE-001')               // direct download

// Update metadata
await client.files.updateMetadata('FILE-001', {
  tags: ['report', 'monthly', 'reviewed'],
  description: 'Monthly report (reviewed)',
})

// Delete
await client.files.deleteFile('FILE-001')           // soft-delete (sets inactive)
await client.files.hardDeleteFile('FILE-001')        // permanent: removes from MinIO

// Bulk delete
await client.files.deleteFiles(['FILE-001', 'FILE-002'])

// Find which documents reference a file
const refs = await client.files.getFileDocuments('FILE-001')

// Integrity and maintenance
const orphans = await client.files.listOrphans({ older_than_hours: 24 })
const dupes = await client.files.findByChecksum('sha256hash')
const integrity = await client.files.checkIntegrity()
// integrity.status: 'healthy' | 'warning' | 'error'
// integrity.issues: [{ type: 'orphan_file' | 'missing_storage' | 'broken_reference', ... }]
```

---

## Registry (Namespaces & IDs)

The Registry is WIP's central ID authority. Services call it with composite keys; the Registry hashes keys, checks for existing entries, and either returns existing IDs or generates new ones (UUID7).

```typescript
// Namespaces
const namespaces = await client.registry.listNamespaces()
const ns = await client.registry.getNamespace('wip')
const stats = await client.registry.getNamespaceStats('wip')

// Create/manage namespaces
await client.registry.createNamespace({
  prefix: 'my-app',
  description: 'My application namespace',
})
await client.registry.updateNamespace('my-app', {
  description: 'Updated description',
  isolation_mode: 'strict',           // 'open' or 'strict'
})
await client.registry.archiveNamespace('my-app')
await client.registry.restoreNamespace('my-app')
await client.registry.deleteNamespace('my-app', 'admin@wip.local')

// Initialize the default WIP namespaces (one-time setup)
await client.registry.initializeWipNamespace()

// Entry lookup
const entry = await client.registry.getEntry('E-001')
const lookup = await client.registry.lookupEntry('E-001')

// Search
const results = await client.registry.unifiedSearch({
  q: 'patient',
  namespaces: ['wip'],
  entity_types: ['documents'],
})
const searchResults = await client.registry.searchEntries('patient record', {
  namespaces: ['wip'],
  entityTypes: ['documents'],
})

// Synonyms: map multiple keys to the same entity
await client.registry.addSynonym({
  target_id: 'E-001',
  synonym_namespace: 'external-system',
  synonym_entity_type: 'patient',
  synonym_composite_key: { mrn: '12345' },
})
await client.registry.removeSynonym({
  target_id: 'E-001',
  synonym_composite_key_hash: 'hash...',
})

// Merge: combine two entries (keeps preferred, deprecates other)
await client.registry.mergeEntries({
  preferred_id: 'E-001',
  deprecated_id: 'E-002',
})

// Deactivate
await client.registry.deactivateEntry('E-001')

// Browse all entries
const entries = await client.registry.listEntries({
  namespace: 'wip',
  entity_type: 'documents',
  page: 1,
  page_size: 50,
})
```

---

## Reporting-Sync (Search & Integrity)

Reporting-Sync maintains a PostgreSQL mirror of MongoDB data via NATS events. Use it for cross-service search, integrity checks, and activity tracking.

```typescript
// Health check
const healthy = await client.reporting.healthCheck()  // returns boolean

// Cross-service search (queries PostgreSQL)
const results = await client.reporting.search({
  query: 'patient',
  types: ['document', 'template', 'terminology'],
  status: 'active',
  limit: 50,
})
// results.results: [{ type, id, value, label, status, description, updated_at }]
// results.counts: { document: 10, template: 2, terminology: 1 }

// Referential integrity check
const integrity = await client.reporting.getIntegrityCheck({
  check_term_refs: true,
  recent_first: true,
  template_limit: 100,
  document_limit: 1000,
})
// integrity.status: 'healthy' | 'warning' | 'error' | 'partial'
// integrity.issues: [{ type, severity, entity_id, field_path, message }]

// Recent activity
const activity = await client.reporting.getRecentActivity({
  types: 'document,template',
  limit: 20,
})
// activity.activities: [{ type, action, entity_id, timestamp, user, version }]

// Reference tracking
const refs = await client.reporting.getEntityReferences('document', 'DOC-001')
const usedBy = await client.reporting.getReferencedBy('terminology', 'T-001', 50)
const termDocs = await client.reporting.getTermDocuments('TERM-001', 100)
```

---

## Error Handling

### Error Hierarchy

All errors extend `WipError`:

| Error Class | HTTP Status | When |
|-------------|------------|------|
| `WipNotFoundError` | 404 | Entity doesn't exist |
| `WipValidationError` | 400, 422 | Invalid request data |
| `WipConflictError` | 409 | Conflict (rare — most conflicts are per-item in BulkResponse) |
| `WipAuthError` | 401, 403 | Bad/missing credentials or insufficient permissions |
| `WipServerError` | 5xx | Server-side error |
| `WipNetworkError` | — | Network timeout or connection failure |
| `WipBulkItemError` | — | Single-item convenience method when the item's `status === "error"` |

```typescript
import {
  WipError, WipNotFoundError, WipValidationError,
  WipAuthError, WipBulkItemError, WipNetworkError,
} from '@wip/client'

try {
  await client.defStore.getTerminology('nonexistent')
} catch (err) {
  if (err instanceof WipNotFoundError) {
    console.log('Not found:', err.message)
  } else if (err instanceof WipAuthError) {
    console.log('Auth failed:', err.statusCode)  // 401 or 403
  } else if (err instanceof WipNetworkError) {
    console.log('Network error:', err.cause)      // original Error
  } else if (err instanceof WipError) {
    console.log('WIP error:', err.statusCode, err.detail)
  }
}
```

### Single-Item vs. Bulk Error Handling

**Single-item convenience methods** throw on error:

```typescript
try {
  // Throws WipBulkItemError if the item fails
  await client.defStore.createTerminology({ value: 'EXISTING', label: 'Dup' })
} catch (err) {
  if (err instanceof WipBulkItemError) {
    console.log(err.index, err.itemStatus, err.message)
  }
}
```

**Bulk methods** return errors in `results[]` — they never throw for item-level failures:

```typescript
const response = await client.defStore.createTerminologies([
  { value: 'A', label: 'A' },
  { value: 'B', label: 'B' },
])

for (const item of response.results) {
  if (item.status === 'error') {
    console.log(`Item ${item.index} failed: ${item.error}`)
  } else {
    console.log(`Item ${item.index}: ${item.status}, id: ${item.id}`)
  }
}
console.log(`${response.succeeded}/${response.total} succeeded`)
```

---

## Utilities

### templateToFormSchema

Converts a WIP Template into framework-agnostic form field descriptors. Works with React, Vue, Svelte, or any UI framework.

```typescript
import { templateToFormSchema } from '@wip/client'

const template = await client.templates.getTemplateByValue('PATIENT_RECORD')
const formFields = templateToFormSchema(template)

// Each field has:
// - name, label, inputType ('text'|'number'|'select'|'file'|'group'|'list'|...)
// - required, defaultValue, isIdentity
// - terminologyCode (for term/select fields)
// - referenceType, targetTemplates (for reference/search fields)
// - fileConfig (for file fields: allowedTypes, maxSizeMb, multiple)
// - validation (pattern, minLength, maxLength, minimum, maximum, enum)
// - semanticType ('email', 'url', 'latitude', etc.)
// - children (for object/group fields)
// - arrayItemType, arrayTerminologyCode (for array/list fields)
```

**Input type mapping:**

| Template Field Type | Form Input Type |
|-------------------|----------------|
| `string` | `text` |
| `number` | `number` |
| `integer` | `integer` |
| `boolean` | `checkbox` |
| `date` | `date` |
| `datetime` | `datetime` |
| `term` | `select` |
| `reference` | `search` |
| `file` | `file` |
| `object` | `group` |
| `array` | `list` |

Identity fields are marked with `isIdentity: true` — use this to visually distinguish fields that determine document uniqueness.

### bulkImport

Batch-process large item lists with progress tracking and concurrency control.

```typescript
import { bulkImport } from '@wip/client'

const result = await bulkImport(
  largeTermList,  // e.g., 10,000 terms
  (batch) => client.defStore.createTerms(terminologyId, batch),
  {
    batchSize: 500,            // items per API call (default: 100)
    concurrency: 1,            // parallel batches (default: 1 — safe for Pi)
    continueOnError: true,     // keep going on failures (default: true)
    onProgress: ({ processed, total, succeeded, failed }) => {
      console.log(`${processed}/${total} (${failed} errors)`)
    },
  },
)
// result: { processed: 10000, total: 10000, succeeded: 9998, failed: 2 }
```

**Concurrency guidance:**
- `concurrency: 1` (default) — sequential, safe for Raspberry Pi
- `concurrency: 2-4` — parallel batches, good for faster hardware; overlaps network I/O with server processing

### resolveReference

Search for documents matching a reference field's target template. Useful for autocomplete/typeahead in forms.

```typescript
import { resolveReference } from '@wip/client'

const matches = await resolveReference(client, 'TPL-PATIENT', 'jane', 10)
// matches: [{ documentId, displayValue, identityFields }]
```

Currently fetches 100 recent documents and filters client-side. For large datasets, consider server-side search via `queryDocuments()`.

---

## Types

All types are exported from `@wip/client`:

```typescript
import type {
  // Client
  WipClient, WipClientConfig, AuthProvider,

  // Bulk operations
  BulkResponse, BulkResultItem, BulkImportProgress,

  // Terminologies & Terms
  Terminology, Term, TerminologyListResponse, TermListResponse,
  CreateTerminologyRequest, CreateTermRequest, UpdateTerminologyRequest,

  // Templates
  Template, FieldDefinition, ValidationRule, TemplateListResponse,
  CreateTemplateRequest, UpdateTemplateRequest,

  // Documents
  Document, DocumentListResponse, DocumentQueryRequest,
  CreateDocumentRequest, DocumentVersionResponse, TableViewResponse,

  // Files
  FileEntity, FileListResponse, FileUploadMetadata, FileIntegrityResponse,

  // Registry
  Namespace, RegistryEntryFull, RegistryLookupResponse, RegistrySearchResponse,

  // Ontology
  Relationship, TraversalResponse,

  // Reporting
  IntegrityCheckResult, SearchResponse, ActivityResponse,

  // Errors
  WipError, WipNotFoundError, WipValidationError, WipAuthError,
  WipConflictError, WipServerError, WipNetworkError, WipBulkItemError,

  // Utilities
  FormField,
} from '@wip/client'
```

---

## Pagination

All list endpoints use consistent pagination:

```typescript
const page = await client.documents.listDocuments({
  page: 2,          // 1-based (default: 1)
  page_size: 25,    // default: 50, max: 100
})

page.items      // Document[]
page.total      // total matching records
page.page       // current page number
page.page_size  // items per page
page.pages      // total number of pages (ceil(total / page_size))
```

---

## BulkResponse Contract

Every write operation returns this shape (or throws for single-item convenience methods):

```typescript
interface BulkResponse {
  results: BulkResultItem[]
  total: number        // items submitted
  succeeded: number    // items that succeeded
  failed: number       // items that failed
  skipped?: number     // items skipped (e.g., duplicates with skip_duplicates=true)
}

interface BulkResultItem {
  index: number        // position in the input array
  status: string       // "created" | "updated" | "deleted" | "error" | "skipped"
  id?: string          // entity ID (on success)
  error?: string       // error message (on failure)
  value?: string       // entity value/code
  version?: number     // entity version (templates, documents)
  is_new?: boolean     // true if newly created (documents)
  identity_hash?: string  // document identity hash
  warnings?: string[]  // non-fatal warnings
}
```

---

## Further Reading

- **API conventions:** `docs/api-conventions.md` — bulk-first convention, BulkResponse contract, pagination rules
- **Data models:** `docs/data-models.md` — document, template, term models in detail
- **Uniqueness rules:** `docs/uniqueness-and-identity.md` — identity hashing, Registry synonyms, ID generation
- **Authentication:** `docs/authentication.md` — OIDC + API key dual auth
- **OpenAPI docs:** `http://localhost:{port}/docs` for each service (interactive Swagger UI)
- **React hooks:** See `@wip/react` for TanStack Query hooks wrapping this client
