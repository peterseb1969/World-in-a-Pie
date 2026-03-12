# @wip/react

React hooks for WIP (World In a Pie) services, powered by TanStack Query v5.

## Quick Start

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createWipClient } from '@wip/client'
import { WipProvider, useTerminologies, useCreateDocument } from '@wip/react'

const queryClient = new QueryClient()
const wipClient = createWipClient({
  baseUrl: 'http://localhost',
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
  const { data, isLoading } = useTerminologies({ status: 'active' })
  const createDoc = useCreateDocument()

  if (isLoading) return <div>Loading...</div>

  return (
    <div>
      {data?.items.map(t => <div key={t.terminology_id}>{t.label}</div>)}
      <button onClick={() => createDoc.mutate({ template_id: 'TPL-1', data: { name: 'Test' } })}>
        Create Document
      </button>
    </div>
  )
}
```

## Installation

```json
{
  "dependencies": {
    "@wip/client": "file:../../libs/wip-client",
    "@wip/react": "file:../../libs/wip-react",
    "@tanstack/react-query": "^5.0.0",
    "react": "^18.0.0"
  }
}
```

## Read Hooks

| Hook | Returns | Stale Time |
|------|---------|-----------|
| `useTerminologies(params?)` | `TerminologyListResponse` | 5min |
| `useTerminology(id)` | `Terminology` | 5min |
| `useTerms(terminologyId, params?)` | `TermListResponse` | 5min |
| `useTerm(id)` | `Term` | 5min |
| `useTemplates(params?)` | `TemplateListResponse` | 5min |
| `useTemplate(id)` | `Template` | 5min |
| `useTemplateByValue(value)` | `Template` | 5min |
| `useDocuments(params?)` | `DocumentListResponse` | 30s |
| `useDocument(id)` | `Document` | 30s |
| `useDocumentVersions(id)` | `DocumentVersionResponse` | 30s |
| `useFiles(params?)` | `FileListResponse` | 10min |
| `useFile(id)` | `FileEntity` | 10min |
| `useDownloadUrl(id)` | `FileDownloadResponse` | 1min |
| `useNamespaces()` | `Namespace[]` | 5min |
| `useRegistrySearch(params)` | `RegistrySearchResponse` | 5min |
| `useIntegrityCheck(params?)` | `IntegrityCheckResult` | 1min |
| `useActivity(params?)` | `ActivityResponse` | 1min |

## Write Hooks (Mutations)

All mutations auto-invalidate relevant query keys on success.

| Hook | Input | Returns |
|------|-------|---------|
| `useCreateTerminology()` | `CreateTerminologyRequest` | `BulkResultItem` |
| `useCreateTerm(terminologyId)` | `CreateTermRequest` | `BulkResultItem` |
| `useCreateTemplate()` | `CreateTemplateRequest` | `BulkResultItem` |
| `useCreateDocument()` | `CreateDocumentRequest` | `BulkResultItem` |
| `useCreateDocuments()` | `CreateDocumentRequest[]` | `BulkResponse` |
| `useUploadFile()` | `{ file, filename?, metadata? }` | `FileEntity` |
| `useDeleteDocument()` | `{ id, updatedBy? }` | `BulkResultItem` |

## Specialized Hooks

```tsx
// Convert template to form schema
const { data: formFields } = useFormSchema('PATIENT_RECORD')

// Bulk import with progress tracking
const { mutate, progress } = useBulkImport({
  writeFn: (batch) => client.defStore.createTerms(tId, batch),
  batchSize: 100,
})
```

## Cache Strategy

- Query keys are structured hierarchically via `wipKeys` factory
- Mutations invalidate parent keys (e.g., creating a term invalidates all term queries)
- Import `wipKeys` for manual cache operations
