# @wip/client

TypeScript client library for WIP (World In a Pie) services. Framework-agnostic, zero runtime dependencies, uses native `fetch`.

## Quick Start

```typescript
import { createWipClient } from '@wip/client'

const client = createWipClient({
  baseUrl: 'http://localhost',
  auth: { type: 'api-key', key: 'dev_master_key_for_testing' },
})

// List terminologies
const terminologies = await client.defStore.listTerminologies({ status: 'active' })

// Create a terminology (single-item convenience — wraps bulk API, throws on error)
const result = await client.defStore.createTerminology({
  value: 'GENDER',
  label: 'Gender',
})
console.log(result.id) // "T-001..."

// Bulk create terms (returns full BulkResponse)
const bulkResult = await client.defStore.createTerms('T-001', [
  { value: 'MALE', label: 'Male' },
  { value: 'FEMALE', label: 'Female' },
])
console.log(bulkResult.succeeded) // 2
```

## Installation

Local package — reference via `file:` in consumer's `package.json`:

```json
{
  "dependencies": {
    "@wip/client": "file:../../libs/wip-client"
  }
}
```

Requires Node.js 18+ (for native `fetch` and `FormData`).

## Configuration

```typescript
const client = createWipClient({
  // Required: base URL of your WIP instance
  baseUrl: 'http://localhost',

  // Auth: API key, OIDC token, or custom AuthProvider
  auth: { type: 'api-key', key: 'your-key' },
  // auth: { type: 'oidc', getToken: () => myOidcLib.getAccessToken() },

  // Optional: request timeout in ms (default: 30000)
  timeout: 30_000,

  // Optional: retry config for GET requests (default: 2 retries)
  retry: { maxRetries: 2, baseDelayMs: 500, maxDelayMs: 5000 },

  // Optional: called on 401/403 responses
  onAuthError: () => { window.location.href = '/login' },
})

// Change auth at runtime
client.setAuth(new ApiKeyAuthProvider('new-key'))
```

## Services

| Service | Property | Port | Description |
|---------|----------|------|-------------|
| Def-Store | `client.defStore` | 8002 | Terminologies, terms, ontology |
| Template-Store | `client.templates` | 8003 | Document schemas |
| Document-Store | `client.documents` | 8004 | Documents, versions, table view |
| File-Store | `client.files` | 8004 | File upload/download (MinIO) |
| Registry | `client.registry` | 8001 | Namespaces, ID management |
| Reporting-Sync | `client.reporting` | 8005 | Integrity, search, activity |

The TypeScript types and method signatures are the definitive API reference — use your editor's autocomplete and go-to-definition. For backend API details, see the OpenAPI docs at `http://localhost:{port}/docs` for each service, and `docs/api-conventions.md` for the bulk-first convention.

### Terminologies & Terms (Def-Store)

```typescript
// Terminologies
const list = await client.defStore.listTerminologies({ status: 'active', search: 'gender' })
const terminology = await client.defStore.getTerminology('T-001')
await client.defStore.createTerminology({ value: 'COUNTRY', label: 'Country' })
await client.defStore.updateTerminology('T-001', { label: 'Updated Label' })

// Terms — single and bulk
await client.defStore.createTerm('T-001', { value: 'UK', label: 'United Kingdom' })
const bulk = await client.defStore.createTerms('T-001', [
  { value: 'US', label: 'United States' },
  { value: 'FR', label: 'France' },
])
await client.defStore.deprecateTerm('TERM-001', { reason: 'Replaced', replaced_by_term_id: 'TERM-002' })

// Import/Export
await client.defStore.importTerminology({ terminology: { value: 'ICD10', label: 'ICD-10' }, terms: [...] })
const exported = await client.defStore.exportTerminology('T-001', { includeRelationships: true })

// Ontology traversal
const ancestors = await client.defStore.getAncestors('TERM-001', { relationship_type: 'is_a' })
const descendants = await client.defStore.getDescendants('TERM-001', { max_depth: 3 })
```

### Templates (Template-Store)

```typescript
const templates = await client.templates.listTemplates({ latest_only: true })
const template = await client.templates.getTemplate('TPL-001')
const byValue = await client.templates.getTemplateByValue('PATIENT_RECORD')
const versions = await client.templates.getTemplateVersions('PATIENT_RECORD')

await client.templates.createTemplate({
  value: 'LAB_RESULT',
  label: 'Lab Result',
  fields: [{ name: 'test_name', label: 'Test', type: 'string', mandatory: true, metadata: {} }],
})
await client.templates.updateTemplate('TPL-001', { label: 'Updated' }) // creates new version

// Inheritance and draft mode
const children = await client.templates.getChildren('TPL-001')
await client.templates.activateTemplate('TPL-DRAFT')
```

### Documents (Document-Store)

```typescript
const docs = await client.documents.listDocuments({ template_id: 'TPL-001', status: 'active' })
const doc = await client.documents.getDocument('DOC-001')

// Create (upsert — same identity fields = new version)
await client.documents.createDocument({
  template_id: 'TPL-001',
  data: { name: 'Jane Doe', email: 'jane@example.com' },
})

// Bulk create
await client.documents.createDocuments([
  { template_id: 'TPL-001', data: { name: 'Alice' } },
  { template_id: 'TPL-001', data: { name: 'Bob' } },
])

// Versions and table view
const versions = await client.documents.getVersions('DOC-001')
const table = await client.documents.getTableView('TPL-001', { status: 'active' })
const csv = await client.documents.exportTableCsv('TPL-001')
```

### Files (File-Store)

```typescript
// Upload (accepts File or Blob)
const file = await client.files.uploadFile(myFile, undefined, {
  description: 'Patient scan',
  tags: ['radiology'],
  category: 'imaging',
})

// Download
const { download_url } = await client.files.getDownloadUrl(file.file_id)
const blob = await client.files.downloadFileContent(file.file_id)

// Metadata and lifecycle
await client.files.updateMetadata(file.file_id, { tags: ['radiology', 'urgent'] })
await client.files.deleteFile(file.file_id)       // soft-delete
await client.files.hardDeleteFile(file.file_id)    // permanent, reclaims storage

// Utilities
const orphans = await client.files.listOrphans({ older_than_hours: 24 })
const integrity = await client.files.checkIntegrity()
```

### Registry

```typescript
const namespaces = await client.registry.listNamespaces()
const ns = await client.registry.getNamespace('wip-terms')
const stats = await client.registry.getNamespaceStats('wip-terms')

// Entry lookup and search
const entry = await client.registry.getEntry('E-001')
const lookup = await client.registry.lookupEntry('E-001')
const results = await client.registry.unifiedSearch({ q: 'patient' })

// Synonyms and merge
await client.registry.addSynonym({ target_id: 'E-001', synonym_namespace: 'external', synonym_entity_type: 'patient', synonym_composite_key: { mrn: '12345' } })
await client.registry.mergeEntries({ preferred_id: 'E-001', deprecated_id: 'E-002' })
```

### Reporting & Integrity

```typescript
const healthy = await client.reporting.healthCheck()
const integrity = await client.reporting.getIntegrityCheck({ check_term_refs: true })
const activity = await client.reporting.getRecentActivity({ limit: 20 })
const search = await client.reporting.search({ query: 'patient', types: ['document', 'template'] })

// Reference tracking
const refs = await client.reporting.getEntityReferences('document', 'DOC-001')
const usedBy = await client.reporting.getReferencedBy('terminology', 'T-001')
```

## Error Handling

```typescript
import { WipNotFoundError, WipValidationError, WipAuthError, WipBulkItemError } from '@wip/client'

try {
  await client.defStore.getTerminology('nonexistent')
} catch (err) {
  if (err instanceof WipNotFoundError) { /* 404 */ }
  if (err instanceof WipValidationError) { /* 400/422 */ }
  if (err instanceof WipAuthError) { /* 401/403 */ }
  if (err instanceof WipBulkItemError) { /* bulk item-level error */ }
}
```

## Utilities

```typescript
import { templateToFormSchema, bulkImport, resolveReference } from '@wip/client'

// Convert template fields to form descriptors
const formFields = templateToFormSchema(template)

// Import items in sequential batches
await bulkImport(items, (batch) => client.defStore.createTerms(tId, batch), {
  batchSize: 100,
  onProgress: ({ processed, total }) => console.log(`${processed}/${total}`),
})

// Resolve document references for autocomplete
const refs = await resolveReference(client, templateId, 'search term')
```

## Type Generation (OpenAPI)

With services running:

```bash
npm run generate-types
# Fetches OpenAPI specs from localhost:800x and generates src/types/generated/
```
