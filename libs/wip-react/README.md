# @wip/react

React hooks for WIP (World In a Pie) services, powered by TanStack Query v5. Wraps `@wip/client` with sensible stale times, automatic cache invalidation, and progress-tracked bulk imports.

## Critical Behaviors

### Mutations Auto-Invalidate Queries

Every mutation hook invalidates related query caches on success. Creating a document automatically refetches any `useDocuments()` queries. You rarely need to manually invalidate.

```tsx
const { data } = useDocuments({ template_id: 'TPL-001' })
const create = useCreateDocument()

// After this succeeds, useDocuments automatically refetches
create.mutate({ template_id: 'TPL-001', data: { name: 'Jane' } })
```

The invalidation is hierarchical — creating a term invalidates `wipKeys.terms.all` (all term queries) **and** `wipKeys.terminologies.detail(id)` (the parent terminology, since its term count changed).

### BulkResponse: Check results[], Not HTTP Status

WIP's bulk-first API always returns HTTP 200. Single-item mutation hooks (`useCreateDocument`, `useCreateTerminology`, etc.) resolve with a `BulkResultItem` — the underlying client throws `WipBulkItemError` if `status === "error"`, which TanStack Query surfaces via `mutation.error`.

Bulk mutation hooks (`useCreateDocuments`) resolve with a full `BulkResponse`. You **must** check `results[i].status` yourself:

```tsx
const createMany = useCreateDocuments()
createMany.mutate(items, {
  onSuccess: (response) => {
    // response.succeeded, response.failed
    const errors = response.results.filter(r => r.status === 'error')
    if (errors.length > 0) {
      // Handle partial failures — HTTP was still 200!
    }
  },
})
```

### Stale Times Are Tuned for WIP's Data Patterns

Documents change frequently (30s stale time). Terminologies and templates change rarely (5min). Files almost never change (10min). Download URLs expire (1min). Override per-hook if needed:

```tsx
// Override stale time for a specific query
const { data } = useDocuments(params, { staleTime: 5_000 })  // 5 seconds
```

### Detail Queries Are Disabled When ID Is Falsy

Hooks like `useTerminology(id)`, `useDocument(id)`, etc. set `enabled: !!id`. They won't fire until you provide a real ID. This is safe for conditional rendering:

```tsx
const [selectedId, setSelectedId] = useState<string | null>(null)
const { data: doc } = useDocument(selectedId ?? '')  // no request until selectedId is set
```

---

## Quick Start

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createWipClient } from '@wip/client'
import { WipProvider, useTerminologies, useCreateDocument } from '@wip/react'

const queryClient = new QueryClient()
const wipClient = createWipClient({
  baseUrl: '',  // In browser: uses window.location.origin (e.g. https://localhost:8443)
  auth: { type: 'api-key', key: 'dev_master_key_for_testing' },
})

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <WipProvider client={wipClient}>
        <MyComponent />
      </WipProvider>
    </QueryClientProvider>
  )
}

function MyComponent() {
  const { data, isLoading, error } = useTerminologies({ status: 'active' })
  const createDoc = useCreateDocument()

  if (isLoading) return <div>Loading...</div>
  if (error) return <div>Error: {error.message}</div>

  return (
    <div>
      {data?.items.map(t => <div key={t.terminology_id}>{t.label}</div>)}
      <button
        disabled={createDoc.isPending}
        onClick={() => createDoc.mutate({
          template_id: 'TPL-1',
          data: { name: 'Test' },
        })}
      >
        Create Document
      </button>
    </div>
  )
}
```

## Installation

Requires three packages:

```json
{
  "dependencies": {
    "@wip/client": "file:../../libs/wip-client",
    "@wip/react": "file:../../libs/wip-react",
    "@tanstack/react-query": "^5.0.0",
    "react": "^18.0.0 || ^19.0.0"
  }
}
```

`@wip/client` and `@tanstack/react-query` are peer dependencies — you must install them alongside `@wip/react`.

## Provider Setup

Wrap your app with both `QueryClientProvider` (from TanStack) and `WipProvider` (from @wip/react). Order matters — `QueryClientProvider` must be the outer wrapper:

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createWipClient } from '@wip/client'
import { WipProvider } from '@wip/react'

const queryClient = new QueryClient()
const wipClient = createWipClient({
  baseUrl: '',  // In browser: uses window.location.origin (e.g. https://localhost:8443)
  auth: { type: 'api-key', key: 'dev_master_key_for_testing' },
})

function Root() {
  return (
    <QueryClientProvider client={queryClient}>
      <WipProvider client={wipClient}>
        <App />
      </WipProvider>
    </QueryClientProvider>
  )
}
```

Access the WIP client directly from any component:

```tsx
import { useWipClient } from '@wip/react'

function MyComponent() {
  const client = useWipClient()  // throws if used outside WipProvider
  // Use client.defStore, client.templates, etc. directly
}
```

---

## Read Hooks

All read hooks return TanStack Query's `UseQueryResult<T>` with `data`, `isLoading`, `error`, `refetch`, etc. Every hook accepts an optional final parameter to override TanStack Query options (staleTime, refetchInterval, etc.).

### Terminologies & Terms

| Hook | Returns | Stale Time | Enabled |
|------|---------|-----------|---------|
| `useTerminologies(params?)` | `TerminologyListResponse` | 5min | always |
| `useTerminology(id)` | `Terminology` | 5min | when `id` is truthy |
| `useTerms(terminologyId, params?)` | `TermListResponse` | 5min | when `terminologyId` is truthy |
| `useTerm(id)` | `Term` | 5min | when `id` is truthy |

```tsx
// List terminologies with pagination
const { data } = useTerminologies({ status: 'active', page: 1, page_size: 25 })
// data.items, data.total, data.pages

// Get single terminology
const { data: terminology } = useTerminology('T-001')

// List terms under a terminology with search
const { data: terms } = useTerms('T-001', { search: 'united', page: 1 })

// Get single term
const { data: term } = useTerm('TERM-001')
```

### Templates

| Hook | Returns | Stale Time | Enabled |
|------|---------|-----------|---------|
| `useTemplates(params?)` | `TemplateListResponse` | 5min | always |
| `useTemplate(id)` | `Template` | 5min | when `id` is truthy |
| `useTemplateByValue(value)` | `Template` | 5min | when `value` is truthy |

```tsx
// List all active templates (latest versions only)
const { data } = useTemplates({ latest_only: true, status: 'active' })

// Get by ID
const { data: template } = useTemplate('TPL-001')

// Get by value code (e.g., 'PATIENT_RECORD')
const { data: template } = useTemplateByValue('PATIENT_RECORD')
```

### Documents

| Hook | Returns | Stale Time | Enabled |
|------|---------|-----------|---------|
| `useDocuments(params?)` | `DocumentListResponse` | 30s | always |
| `useDocument(id)` | `Document` | 30s | when `id` is truthy |
| `useQueryDocuments(query)` | `DocumentListResponse` | 30s | when `query.template_id` or `query.filters.length > 0` |
| `useDocumentVersions(id)` | `DocumentVersionResponse` | 30s | when `id` is truthy |

```tsx
// List documents for a template
const { data } = useDocuments({ template_id: 'TPL-001', status: 'active' })

// Get single document
const { data: doc } = useDocument('DOC-001')

// Complex query with filters
const { data } = useQueryDocuments({
  template_id: 'TPL-001',
  filters: [
    { field: 'data.country', operator: 'eq', value: 'US' },
    { field: 'data.age', operator: 'gte', value: 18 },
  ],
  sort_by: 'created_at',
  sort_order: 'desc',
})

// Document version history
const { data: versions } = useDocumentVersions('DOC-001')
```

### Files

| Hook | Returns | Stale Time | Enabled |
|------|---------|-----------|---------|
| `useFiles(params?)` | `FileListResponse` | 10min | always |
| `useFile(id)` | `FileEntity` | 10min | when `id` is truthy |
| `useDownloadUrl(id)` | `FileDownloadResponse` | 1min | when `id` is truthy |

```tsx
const { data: files } = useFiles({ status: 'active' })
const { data: file } = useFile('FILE-001')

// Download URL has short stale time because URLs expire
const { data } = useDownloadUrl('FILE-001')
// data.download_url — use within its validity window
```

### Registry & Namespaces

| Hook | Returns | Stale Time | Enabled |
|------|---------|-----------|---------|
| `useNamespaces()` | `Namespace[]` | 5min | always |
| `useRegistrySearch(params)` | `RegistrySearchResponse` | 5min | when `params.q` is truthy |

```tsx
const { data: namespaces } = useNamespaces()

const { data: results } = useRegistrySearch({
  q: 'patient',
  namespaces: ['wip'],
  entity_types: ['documents'],
})
```

### Reporting

| Hook | Returns | Stale Time | Enabled |
|------|---------|-----------|---------|
| `useIntegrityCheck(params?)` | `IntegrityCheckResult` | 1min | always |
| `useActivity(params?)` | `ActivityResponse` | 1min | always |

```tsx
const { data: integrity } = useIntegrityCheck({ check_term_refs: true })
// integrity.status: 'healthy' | 'warning' | 'error'
// integrity.issues: [{ type, severity, entity_id, message }]

const { data: activity } = useActivity({ types: 'document,template', limit: 20 })
// activity.activities: [{ type, action, entity_id, timestamp, user }]
```

---

## Write Hooks (Mutations)

All mutations return TanStack Query's `UseMutationResult` with `mutate`, `mutateAsync`, `isPending`, `error`, `data`, etc. Every hook accepts an optional parameter to override TanStack Query mutation options (`onSuccess`, `onError`, `onSettled`, etc.).

| Hook | Input | Returns | Invalidates |
|------|-------|---------|-------------|
| `useCreateTerminology()` | `CreateTerminologyRequest` | `BulkResultItem` | `terminologies.all` |
| `useCreateTerm(terminologyId)` | `CreateTermRequest` | `BulkResultItem` | `terms.all` + `terminologies.detail(id)` |
| `useCreateTemplate()` | `CreateTemplateRequest` | `BulkResultItem` | `templates.all` |
| `useCreateDocument()` | `CreateDocumentRequest` | `BulkResultItem` | `documents.all` |
| `useCreateDocuments()` | `CreateDocumentRequest[]` | `BulkResponse` | `documents.all` |
| `useUploadFile()` | `{ file, filename?, metadata? }` | `FileEntity` | `files.all` |
| `useDeleteDocument()` | `{ id, updatedBy? }` | `BulkResultItem` | `documents.all` |

### Single-Item Mutations

```tsx
const createTerm = useCreateTerminology()

// Fire-and-forget
createTerm.mutate({ value: 'GENDER', label: 'Gender' })

// With callbacks
createTerm.mutate(
  { value: 'GENDER', label: 'Gender' },
  {
    onSuccess: (result) => {
      console.log('Created:', result.id, result.status)
    },
    onError: (error) => {
      // WipBulkItemError if the item failed
      console.error('Failed:', error.message)
    },
  },
)

// Async/await
const result = await createTerm.mutateAsync({ value: 'GENDER', label: 'Gender' })
```

### Bulk Mutations

```tsx
const createDocs = useCreateDocuments()

createDocs.mutate(
  [
    { template_id: 'TPL-1', data: { name: 'Alice' } },
    { template_id: 'TPL-1', data: { name: 'Bob' } },
  ],
  {
    onSuccess: (response) => {
      console.log(`${response.succeeded}/${response.total} succeeded`)
      // Check for partial failures
      const errors = response.results.filter(r => r.status === 'error')
      if (errors.length > 0) {
        console.warn('Some items failed:', errors)
      }
    },
  },
)
```

### File Upload

```tsx
const upload = useUploadFile()

const handleUpload = (file: File) => {
  upload.mutate({
    file,
    filename: file.name,
    metadata: {
      description: 'Patient scan',
      tags: ['radiology'],
      category: 'imaging',
    },
  })
}

// upload.isPending — show spinner
// upload.data — FileEntity on success
```

### Delete

```tsx
const deleteDoc = useDeleteDocument()

deleteDoc.mutate(
  { id: 'DOC-001', updatedBy: 'user@example.com' },
  {
    onSuccess: () => {
      // Documents list auto-refetches via cache invalidation
      navigate('/documents')
    },
  },
)
```

---

## Specialized Hooks

### useFormSchema

Fetches a template by value and converts it to framework-agnostic form field descriptors. Combines `getTemplateByValue()` + `templateToFormSchema()` from @wip/client in a single hook.

```tsx
import { useFormSchema } from '@wip/react'

function PatientForm() {
  const { data: fields, isLoading } = useFormSchema('PATIENT_RECORD')

  if (isLoading || !fields) return <div>Loading form...</div>

  return (
    <form>
      {fields.map(field => (
        <div key={field.name}>
          <label>
            {field.label}
            {field.required && ' *'}
            {field.isIdentity && ' (ID)'}
          </label>
          {field.inputType === 'text' && <input type="text" name={field.name} />}
          {field.inputType === 'number' && <input type="number" name={field.name} />}
          {field.inputType === 'checkbox' && <input type="checkbox" name={field.name} />}
          {field.inputType === 'select' && (
            // field.terminologyCode tells you which terminology to load
            <TermSelect terminologyCode={field.terminologyCode!} name={field.name} />
          )}
          {field.inputType === 'file' && (
            // field.fileConfig.allowedTypes, .maxSizeMb, .multiple
            <FileInput config={field.fileConfig!} name={field.name} />
          )}
        </div>
      ))}
    </form>
  )
}
```

**FormField properties:** `name`, `label`, `inputType` (text/number/integer/checkbox/date/datetime/select/search/file/group/list), `required`, `defaultValue`, `isIdentity`, `terminologyCode`, `referenceType`, `targetTemplates`, `targetTerminologies`, `fileConfig`, `validation` (pattern, minLength, maxLength, minimum, maximum, enum), `semanticType`, `children` (for groups), `arrayItemType`.

- **Stale time:** 5min (template stale time)
- **Enabled:** only when `templateValue` is truthy
- **Query key:** `[...wipKeys.templates.byValue(value), 'form-schema']`

### useBulkImport

Progress-tracked batch import with configurable batch size. Wraps `bulkImport()` from @wip/client in a mutation hook.

```tsx
import { useBulkImport, useWipClient } from '@wip/react'

function ImportTerms({ terminologyId }: { terminologyId: string }) {
  const client = useWipClient()

  const { mutate, isPending, progress, error, data } = useBulkImport({
    writeFn: (batch) => client.defStore.createTerms(terminologyId, batch),
    batchSize: 100,
    continueOnError: true,
    invalidateKeys: wipKeys.terms.all,  // what to refetch on completion
  })

  const handleImport = (terms: CreateTermRequest[]) => {
    mutate(terms)  // auto-batched internally
  }

  return (
    <div>
      <button onClick={() => handleImport(myTerms)} disabled={isPending}>
        Import {myTerms.length} terms
      </button>

      {progress && (
        <div>
          {progress.processed}/{progress.total}
          ({progress.succeeded} ok, {progress.failed} errors)
        </div>
      )}

      {data && (
        <div>
          Done: {data.succeeded}/{data.total} imported
        </div>
      )}
    </div>
  )
}
```

**Options:**
- `writeFn: (batch: T[]) => Promise<BulkResponse>` — the API call for each batch
- `batchSize?: number` — items per batch (default: 100)
- `continueOnError?: boolean` — keep processing after errors (default: false)
- `invalidateKeys?: readonly unknown[]` — query keys to invalidate on success (default: `wipKeys.all`)

**Returns** (extends `UseMutationResult`):
- `progress: BulkImportProgress | null` — live progress: `{ processed, total, succeeded, failed }`
- `reset()` — clear progress and mutation state

Progress is cleared automatically 2 seconds after completion.

---

## Cache Strategy

### Query Key Factory

All query keys are structured hierarchically via `wipKeys`. Use them for manual cache operations:

```tsx
import { wipKeys } from '@wip/react'
import { useQueryClient } from '@tanstack/react-query'

function RefreshButton() {
  const queryClient = useQueryClient()

  return (
    <button onClick={() => {
      // Invalidate all document queries (lists, details, versions, table views)
      queryClient.invalidateQueries({ queryKey: wipKeys.documents.all })
    }}>
      Refresh Documents
    </button>
  )
}
```

### Key Hierarchy

```
wipKeys.all                                    → ['wip']
wipKeys.terminologies.all                      → ['wip', 'terminologies']
wipKeys.terminologies.list(params?)            → ['wip', 'terminologies', 'list', params]
wipKeys.terminologies.detail(id)               → ['wip', 'terminologies', 'detail', id]
wipKeys.terms.all                              → ['wip', 'terms']
wipKeys.terms.list(terminologyId, params?)     → ['wip', 'terms', 'list', terminologyId, params]
wipKeys.terms.detail(id)                       → ['wip', 'terms', 'detail', id]
wipKeys.templates.all                          → ['wip', 'templates']
wipKeys.templates.list(params?)                → ['wip', 'templates', 'list', params]
wipKeys.templates.detail(id)                   → ['wip', 'templates', 'detail', id]
wipKeys.templates.byValue(value)               → ['wip', 'templates', 'by-value', value]
wipKeys.documents.all                          → ['wip', 'documents']
wipKeys.documents.list(params?)                → ['wip', 'documents', 'list', params]
wipKeys.documents.detail(id)                   → ['wip', 'documents', 'detail', id]
wipKeys.documents.versions(id)                 → ['wip', 'documents', 'versions', id]
wipKeys.documents.tableView(templateId, params?) → ['wip', 'documents', 'table', templateId, params]
wipKeys.files.all                              → ['wip', 'files']
wipKeys.files.list(params?)                    → ['wip', 'files', 'list', params]
wipKeys.files.detail(id)                       → ['wip', 'files', 'detail', id]
wipKeys.files.downloadUrl(id)                  → ['wip', 'files', 'download-url', id]
wipKeys.registry.all                           → ['wip', 'registry']
wipKeys.registry.namespaces()                  → ['wip', 'registry', 'namespaces']
wipKeys.registry.namespace(prefix)             → ['wip', 'registry', 'namespaces', prefix]
wipKeys.registry.entries(params?)              → ['wip', 'registry', 'entries', params]
wipKeys.registry.entry(id)                     → ['wip', 'registry', 'entries', id]
wipKeys.registry.search(params?)               → ['wip', 'registry', 'search', params]
wipKeys.reporting.all                          → ['wip', 'reporting']
wipKeys.reporting.integrity(params?)           → ['wip', 'reporting', 'integrity', params]
wipKeys.reporting.activity(params?)            → ['wip', 'reporting', 'activity', params]
wipKeys.reporting.search(params?)              → ['wip', 'reporting', 'search', params]
```

Invalidating a parent key cascades to all children. For example, `queryClient.invalidateQueries({ queryKey: wipKeys.templates.all })` invalidates every template list, detail, and byValue query.

### Stale Time Defaults

| Domain | Stale Time | Rationale |
|--------|-----------|-----------|
| Terminologies | 5 min | Change infrequently after initial setup |
| Terms | 5 min | Change infrequently after initial import |
| Templates | 5 min | Structural changes are rare |
| Documents | 30 sec | Actively edited, need fresh data |
| Files | 10 min | Rarely change after upload |
| Download URLs | 1 min | MinIO pre-signed URLs are time-limited |
| Registry | 5 min | Namespace config rarely changes |
| Reporting | 1 min | Activity/integrity data should be recent |

Override on any hook:

```tsx
const { data } = useDocuments(params, { staleTime: 5_000 })       // 5 seconds
const { data } = useTemplates(params, { staleTime: Infinity })     // never refetch
const { data } = useActivity(params, { refetchInterval: 10_000 }) // poll every 10s
```

### Mutation Auto-Invalidation Map

| Mutation | Invalidates |
|----------|-------------|
| `useCreateTerminology` | `wipKeys.terminologies.all` |
| `useCreateTerm(terminologyId)` | `wipKeys.terms.all` + `wipKeys.terminologies.detail(terminologyId)` |
| `useCreateTemplate` | `wipKeys.templates.all` |
| `useCreateDocument` | `wipKeys.documents.all` |
| `useCreateDocuments` | `wipKeys.documents.all` |
| `useUploadFile` | `wipKeys.files.all` |
| `useDeleteDocument` | `wipKeys.documents.all` |

---

## Exports

```typescript
// Provider & client access
export { WipProvider, useWipClient, type WipProviderProps }

// Cache management
export { wipKeys, STALE_TIMES }

// Read hooks — Terminologies & Terms
export { useTerminologies, useTerminology }
export { useTerms, useTerm }

// Read hooks — Templates
export { useTemplates, useTemplate, useTemplateByValue }

// Read hooks — Documents
export { useDocuments, useDocument, useQueryDocuments, useDocumentVersions }

// Read hooks — Files
export { useFiles, useFile, useDownloadUrl }

// Read hooks — Registry & Reporting
export { useNamespaces, useRegistrySearch }
export { useIntegrityCheck, useActivity }

// Write hooks
export {
  useCreateTerminology, useCreateTerm,
  useCreateTemplate,
  useCreateDocument, useCreateDocuments,
  useUploadFile, useDeleteDocument,
}

// Specialized hooks
export { useFormSchema, useBulkImport }
```

All types come from `@wip/client` — import them directly:

```typescript
import type { Template, Document, BulkResponse } from '@wip/client'
```

---

## Common Patterns

### Template-Driven Document Form

```tsx
function DocumentForm({ templateValue }: { templateValue: string }) {
  const { data: fields } = useFormSchema(templateValue)
  const { data: template } = useTemplateByValue(templateValue)
  const createDoc = useCreateDocument()

  const handleSubmit = (formData: Record<string, unknown>) => {
    if (!template) return
    createDoc.mutate(
      { template_id: template.template_id, data: formData },
      {
        onSuccess: (result) => {
          console.log('Created:', result.id, 'version:', result.version)
        },
      },
    )
  }

  if (!fields) return <div>Loading...</div>
  return <DynamicForm fields={fields} onSubmit={handleSubmit} />
}
```

### Term Selector (Dropdown)

```tsx
function TermSelect({ terminologyId, value, onChange }: {
  terminologyId: string
  value: string
  onChange: (value: string) => void
}) {
  const { data } = useTerms(terminologyId, { status: 'active', page_size: 100 })

  return (
    <select value={value} onChange={e => onChange(e.target.value)}>
      <option value="">Select...</option>
      {data?.items.map(term => (
        <option key={term.term_id} value={term.value}>{term.label || term.value}</option>
      ))}
    </select>
  )
}
```

### Document List with Pagination

```tsx
function DocumentList({ templateId }: { templateId: string }) {
  const [page, setPage] = useState(1)
  const { data, isLoading } = useDocuments({
    template_id: templateId,
    status: 'active',
    page,
    page_size: 25,
  })

  if (isLoading) return <div>Loading...</div>

  return (
    <div>
      {data?.items.map(doc => (
        <div key={doc.document_id}>{JSON.stringify(doc.data)}</div>
      ))}
      <div>
        Page {data?.page} of {data?.pages}
        <button disabled={page <= 1} onClick={() => setPage(p => p - 1)}>Prev</button>
        <button disabled={page >= (data?.pages ?? 1)} onClick={() => setPage(p => p + 1)}>Next</button>
      </div>
    </div>
  )
}
```

---

## Further Reading

- **@wip/client README** — complete service API reference, error handling, utilities, type exports
- **TanStack Query docs** — https://tanstack.com/query/latest — for advanced patterns (optimistic updates, prefetching, suspense)
- **API conventions:** `docs/api-conventions.md` — bulk-first convention, BulkResponse contract
- **Data models:** `docs/data-models.md` — document, template, term models
