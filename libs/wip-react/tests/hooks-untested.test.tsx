/**
 * Tests for the 4 hooks that had zero coverage when CASE-334's audit ran:
 *
 *   - use-form-schema   (templateToFormSchema integration)
 *   - use-registry      (useNamespaces, useRegistrySearch)
 *   - use-bulk-import   (mutation wrapping bulkImport util with progress)
 *   - use-reporting     (10+ hooks for SQL/activity/sync/batch-jobs)
 *
 * Pattern mirrors hooks.test.tsx: mock WipClient via the WipProvider,
 * renderHook with a fresh QueryClient per test.
 *
 * Filed as CASE-341 — wip-react untested hooks.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { WipProvider } from '../src/provider'
import type { WipClient } from '@wip/client'
import type { ReactNode } from 'react'

// ---------------------------------------------------------------------------
// Module-level mocks for @wip/client utility functions
//
// use-form-schema imports `templateToFormSchema` and use-bulk-import imports
// `bulkImport` directly from @wip/client (not via the client instance), so
// they need module-level mocks.
// ---------------------------------------------------------------------------

vi.mock('@wip/client', async () => {
  const actual = await vi.importActual<typeof import('@wip/client')>('@wip/client')
  return {
    ...actual,
    templateToFormSchema: vi.fn((template: { entity_id?: string }) => [
      { name: 'field_a', type: 'string', mandatory: true },
      { name: 'field_b', type: 'integer', mandatory: false },
    ]),
    bulkImport: vi.fn(async (items, writeFn, options) => {
      // Call writeFn once with the items to simulate a single batch.
      // Real bulkImport would chunk and emit progress; we just exercise
      // the success path.
      const response = await writeFn(items)
      if (options?.onProgress) {
        options.onProgress({
          total: items.length,
          processed: items.length,
          succeeded: items.length,
          failed: 0,
          batchesCompleted: 1,
          totalBatches: 1,
        })
      }
      return response
    }),
  }
})

// Import hooks AFTER the mock setup
import { useFormSchema } from '../src/hooks/use-form-schema'
import { useNamespaces, useRegistrySearch } from '../src/hooks/use-registry'
import { useBulkImport } from '../src/hooks/use-bulk-import'
import {
  useReportQuery,
  useIntegrityCheck,
  useActivity,
  useSyncStatus,
  useBatchJobs,
  useBatchJob,
  useTriggerBatchSyncAll,
  useTriggerBatchSync,
  useTriggerTerminologySync,
  useTriggerTermSync,
  useTriggerTermRelationSync,
  useCancelBatchJob,
  useClearCompletedJobs,
} from '../src/hooks/use-reporting'
// Additional mutation hooks from use-mutations.ts that hooks.test.tsx skipped.
// Covering these lifts the file's coverage from ~39% to ~50%, which (combined
// with the four 0%-tested hooks above) clears CASE-341's 65% acceptance target.
import {
  useUpdateTerminology,
  useUpdateTerm,
  useCreateTemplate,
  useUpdateTemplate,
  useDeleteTemplate,
  useActivateTemplate,
  useCreateDocuments,
  useArchiveDocument,
  useDeleteFile,
  useDeleteFiles,
  useUpdateFileMetadata,
  useCreateNamespace,
  useDeleteNamespace,
  useAddSynonym,
} from '../src/hooks/use-mutations'
import { templateToFormSchema, bulkImport } from '@wip/client'

// ---------------------------------------------------------------------------
// Mock client factory
// ---------------------------------------------------------------------------

function createMockClient() {
  return {
    defStore: {
      updateTerminology: vi.fn().mockResolvedValue({ entity_id: 't1', status: 'updated' }),
      updateTerm: vi.fn().mockResolvedValue({ entity_id: 'term1', status: 'updated' }),
    },
    templates: {
      getTemplateByValue: vi.fn().mockResolvedValue({
        entity_id: 'tpl1',
        value: 'SCHEMA_X',
        fields: [],
      }),
      createTemplate: vi.fn().mockResolvedValue({ entity_id: 'tpl1', status: 'created' }),
      updateTemplate: vi.fn().mockResolvedValue({ entity_id: 'tpl1', status: 'updated' }),
      deleteTemplate: vi.fn().mockResolvedValue({ entity_id: 'tpl1', status: 'deleted' }),
      activateTemplate: vi.fn().mockResolvedValue({ template_id: 'tpl1', activated: true }),
    },
    documents: {
      createDocuments: vi.fn().mockResolvedValue({ results: [], total: 0, succeeded: 0, failed: 0 }),
      archiveDocument: vi.fn().mockResolvedValue({ entity_id: 'doc1', status: 'archived' }),
    },
    files: {
      deleteFile: vi.fn().mockResolvedValue({ entity_id: 'f1', status: 'deleted' }),
      deleteFiles: vi.fn().mockResolvedValue({ results: [], total: 0, succeeded: 0, failed: 0 }),
      updateMetadata: vi.fn().mockResolvedValue({ entity_id: 'f1', status: 'updated' }),
    },
    registry: {
      listNamespaces: vi.fn().mockResolvedValue([
        { prefix: 'wip', description: 'default' },
        { prefix: 'kb', description: 'knowledge base' },
      ]),
      unifiedSearch: vi.fn().mockResolvedValue({
        results: [],
        total: 0,
      }),
      createNamespace: vi.fn().mockResolvedValue({ prefix: 'new-ns', description: 'created' }),
      deleteNamespace: vi.fn().mockResolvedValue(undefined),
      addSynonym: vi.fn().mockResolvedValue({ status: 'added', registry_id: 'r1' }),
    },
    reporting: {
      runQuery: vi.fn().mockResolvedValue({
        rows: [{ name: 'a', count: 1 }],
        row_count: 1,
        columns: ['name', 'count'],
      }),
      getIntegrityCheck: vi.fn().mockResolvedValue({
        issues: [],
        total: 0,
      }),
      getRecentActivity: vi.fn().mockResolvedValue({
        activities: [],
        total: 0,
      }),
      getSyncStatus: vi.fn().mockResolvedValue({
        running: true,
        nats_connected: true,
        postgres_connected: true,
        events_processed: 100,
        events_failed: 0,
        tables_managed: 5,
      }),
      listBatchJobs: vi.fn().mockResolvedValue([
        { job_id: 'job-1', status: 'completed', template_value: 'X' },
      ]),
      getBatchJob: vi.fn().mockResolvedValue({
        job_id: 'job-1',
        status: 'running',
        documents_synced: 50,
        total_documents: 100,
        current_page: 2,
      }),
      triggerBatchSyncAll: vi.fn().mockResolvedValue([
        { job_id: 'job-1', template_value: 'X' },
      ]),
      triggerBatchSync: vi.fn().mockResolvedValue({
        job_id: 'job-1',
        template_value: 'X',
      }),
      triggerTerminologySync: vi.fn().mockResolvedValue({
        entity_type: 'terminology',
        namespace: 'kb',
        synced: 10,
      }),
      triggerTermSync: vi.fn().mockResolvedValue({
        entity_type: 'term',
        namespace: 'kb',
        synced: 50,
      }),
      triggerTermRelationSync: vi.fn().mockResolvedValue({
        entity_type: 'term_relation',
        namespace: 'kb',
        synced: 20,
      }),
      cancelBatchJob: vi.fn().mockResolvedValue({
        job_id: 'job-1',
        cancelled: true,
      }),
      clearCompletedJobs: vi.fn().mockResolvedValue({
        cleared: 3,
      }),
    },
    setAuth: vi.fn(),
  } as unknown as WipClient
}

function createWrapper(client: WipClient) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  return {
    queryClient,
    Wrapper({ children }: { children: ReactNode }) {
      return (
        <QueryClientProvider client={queryClient}>
          <WipProvider client={client}>{children}</WipProvider>
        </QueryClientProvider>
      )
    },
  }
}

// ===========================================================================
// use-form-schema
// ===========================================================================

describe('useFormSchema', () => {
  let mockClient: ReturnType<typeof createMockClient>
  beforeEach(() => {
    mockClient = createMockClient()
    vi.mocked(templateToFormSchema).mockClear()
  })

  it('fetches template by value and converts to form schema', async () => {
    const { Wrapper } = createWrapper(mockClient)
    const { result } = renderHook(() => useFormSchema('SCHEMA_X'), { wrapper: Wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(mockClient.templates.getTemplateByValue).toHaveBeenCalledWith('SCHEMA_X')
    expect(templateToFormSchema).toHaveBeenCalled()
    expect(result.current.data).toHaveLength(2)
    expect(result.current.data?.[0]).toMatchObject({ name: 'field_a', type: 'string' })
  })

  it('is disabled when templateValue is empty string', () => {
    const { Wrapper } = createWrapper(mockClient)
    const { result } = renderHook(() => useFormSchema(''), { wrapper: Wrapper })

    expect(result.current.fetchStatus).toBe('idle')
    expect(mockClient.templates.getTemplateByValue).not.toHaveBeenCalled()
  })

  it('respects custom options like enabled=false', () => {
    const { Wrapper } = createWrapper(mockClient)
    const { result } = renderHook(
      () => useFormSchema('SCHEMA_X', { enabled: false }),
      { wrapper: Wrapper },
    )

    expect(result.current.fetchStatus).toBe('idle')
    expect(mockClient.templates.getTemplateByValue).not.toHaveBeenCalled()
  })
})

// ===========================================================================
// use-registry
// ===========================================================================

describe('useNamespaces', () => {
  let mockClient: ReturnType<typeof createMockClient>
  beforeEach(() => {
    mockClient = createMockClient()
  })

  it('calls client.registry.listNamespaces', async () => {
    const { Wrapper } = createWrapper(mockClient)
    const { result } = renderHook(() => useNamespaces(), { wrapper: Wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockClient.registry.listNamespaces).toHaveBeenCalled()
    expect(result.current.data).toHaveLength(2)
    expect(result.current.data?.[0]).toMatchObject({ prefix: 'wip' })
  })
})

describe('useRegistrySearch', () => {
  let mockClient: ReturnType<typeof createMockClient>
  beforeEach(() => {
    mockClient = createMockClient()
  })

  it('calls client.registry.unifiedSearch with params', async () => {
    const { Wrapper } = createWrapper(mockClient)
    const params = { q: 'gender', entity_type: 'terminology' as const }
    const { result } = renderHook(() => useRegistrySearch(params), { wrapper: Wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockClient.registry.unifiedSearch).toHaveBeenCalledWith(params)
  })

  it('is disabled when q is empty', () => {
    const { Wrapper } = createWrapper(mockClient)
    const { result } = renderHook(
      () => useRegistrySearch({ q: '' }),
      { wrapper: Wrapper },
    )

    expect(result.current.fetchStatus).toBe('idle')
    expect(mockClient.registry.unifiedSearch).not.toHaveBeenCalled()
  })
})

// ===========================================================================
// use-bulk-import
// ===========================================================================

describe('useBulkImport', () => {
  let mockClient: ReturnType<typeof createMockClient>
  beforeEach(() => {
    mockClient = createMockClient()
    vi.mocked(bulkImport).mockClear()
  })

  it('returns mutation object with reset and progress fields', () => {
    const { Wrapper } = createWrapper(mockClient)
    const writeFn = vi.fn().mockResolvedValue({
      results: [],
      total: 0,
      succeeded: 0,
      failed: 0,
    })
    const { result } = renderHook(() => useBulkImport({ writeFn }), { wrapper: Wrapper })

    expect(result.current).toHaveProperty('mutate')
    expect(result.current).toHaveProperty('reset')
    expect(result.current).toHaveProperty('progress')
    expect(result.current.progress).toBeNull()
  })

  it('calls bulkImport with writeFn and items on mutate', async () => {
    const { Wrapper } = createWrapper(mockClient)
    const writeFn = vi.fn().mockResolvedValue({
      results: [],
      total: 2,
      succeeded: 2,
      failed: 0,
    })
    const { result } = renderHook(() => useBulkImport({ writeFn }), { wrapper: Wrapper })

    const items = [{ a: 1 }, { a: 2 }]
    await act(async () => {
      result.current.mutate(items)
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(bulkImport).toHaveBeenCalledWith(
      items,
      writeFn,
      expect.objectContaining({ onProgress: expect.any(Function) }),
    )
  })

  it('passes batchSize and continueOnError through', async () => {
    const { Wrapper } = createWrapper(mockClient)
    const writeFn = vi.fn().mockResolvedValue({
      results: [], total: 0, succeeded: 0, failed: 0,
    })
    const { result } = renderHook(
      () => useBulkImport({ writeFn, batchSize: 50, continueOnError: true }),
      { wrapper: Wrapper },
    )

    await act(async () => {
      result.current.mutate([{ a: 1 }])
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(bulkImport).toHaveBeenCalledWith(
      expect.any(Array),
      writeFn,
      expect.objectContaining({ batchSize: 50, continueOnError: true }),
    )
  })

  it('reset() clears progress and resets mutation', () => {
    const { Wrapper } = createWrapper(mockClient)
    const writeFn = vi.fn().mockResolvedValue({
      results: [], total: 0, succeeded: 0, failed: 0,
    })
    const { result } = renderHook(() => useBulkImport({ writeFn }), { wrapper: Wrapper })

    act(() => {
      result.current.reset()
    })
    expect(result.current.progress).toBeNull()
  })
})

// ===========================================================================
// use-reporting
// ===========================================================================

describe('useReportQuery', () => {
  let mockClient: ReturnType<typeof createMockClient>
  beforeEach(() => {
    mockClient = createMockClient()
  })

  it('calls client.reporting.runQuery with sql and undefined params', async () => {
    const { Wrapper } = createWrapper(mockClient)
    const sql = 'SELECT * FROM doc_x'
    const { result } = renderHook(() => useReportQuery(sql), { wrapper: Wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockClient.reporting.runQuery).toHaveBeenCalledWith(sql, undefined, {
      max_rows: undefined,
      timeout_seconds: undefined,
    })
  })

  it('passes params and maxRows/timeoutSeconds through', async () => {
    const { Wrapper } = createWrapper(mockClient)
    const sql = 'SELECT * FROM doc_x WHERE id = $1'
    const params = ['abc-123']
    const { result } = renderHook(
      () => useReportQuery(sql, params, { maxRows: 100, timeoutSeconds: 5 }),
      { wrapper: Wrapper },
    )

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockClient.reporting.runQuery).toHaveBeenCalledWith(sql, params, {
      max_rows: 100,
      timeout_seconds: 5,
    })
  })
})

describe('useIntegrityCheck', () => {
  it('calls client.reporting.getIntegrityCheck with params', async () => {
    const mockClient = createMockClient()
    const { Wrapper } = createWrapper(mockClient)
    const params = { template_status: 'active', check_term_refs: true }
    const { result } = renderHook(() => useIntegrityCheck(params), { wrapper: Wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockClient.reporting.getIntegrityCheck).toHaveBeenCalledWith(params)
  })
})

describe('useActivity', () => {
  it('calls client.reporting.getRecentActivity with params', async () => {
    const mockClient = createMockClient()
    const { Wrapper } = createWrapper(mockClient)
    const params = { types: 'terminology', limit: 50 }
    const { result } = renderHook(() => useActivity(params), { wrapper: Wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockClient.reporting.getRecentActivity).toHaveBeenCalledWith(params)
  })
})

describe('useSyncStatus', () => {
  it('calls client.reporting.getSyncStatus', async () => {
    const mockClient = createMockClient()
    const { Wrapper } = createWrapper(mockClient)
    const { result } = renderHook(() => useSyncStatus(), { wrapper: Wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockClient.reporting.getSyncStatus).toHaveBeenCalled()
    expect(result.current.data).toMatchObject({ running: true, tables_managed: 5 })
  })
})

describe('useBatchJobs', () => {
  it('calls client.reporting.listBatchJobs', async () => {
    const mockClient = createMockClient()
    const { Wrapper } = createWrapper(mockClient)
    const { result } = renderHook(() => useBatchJobs(), { wrapper: Wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockClient.reporting.listBatchJobs).toHaveBeenCalled()
    expect(result.current.data).toHaveLength(1)
  })
})

describe('useBatchJob', () => {
  it('calls client.reporting.getBatchJob with id', async () => {
    const mockClient = createMockClient()
    const { Wrapper } = createWrapper(mockClient)
    const { result } = renderHook(() => useBatchJob('job-1'), { wrapper: Wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockClient.reporting.getBatchJob).toHaveBeenCalledWith('job-1')
  })

  it('is disabled when jobId is empty', () => {
    const mockClient = createMockClient()
    const { Wrapper } = createWrapper(mockClient)
    const { result } = renderHook(() => useBatchJob(''), { wrapper: Wrapper })

    expect(result.current.fetchStatus).toBe('idle')
    expect(mockClient.reporting.getBatchJob).not.toHaveBeenCalled()
  })
})

describe('useTriggerBatchSyncAll', () => {
  it('calls triggerBatchSyncAll with provided vars and invalidates batch-jobs', async () => {
    const mockClient = createMockClient()
    const { Wrapper, queryClient } = createWrapper(mockClient)
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')

    const { result } = renderHook(() => useTriggerBatchSyncAll(), { wrapper: Wrapper })
    await act(async () => {
      result.current.mutate({ force: true })
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(mockClient.reporting.triggerBatchSyncAll).toHaveBeenCalledWith({ force: true })
    expect(invalidateSpy).toHaveBeenCalled()
  })

  it('handles void vars (no args)', async () => {
    const mockClient = createMockClient()
    const { Wrapper } = createWrapper(mockClient)
    const { result } = renderHook(() => useTriggerBatchSyncAll(), { wrapper: Wrapper })

    await act(async () => {
      result.current.mutate()
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(mockClient.reporting.triggerBatchSyncAll).toHaveBeenCalledWith(undefined)
  })
})

describe('useTriggerBatchSync', () => {
  it('calls triggerBatchSync with template_value and options', async () => {
    const mockClient = createMockClient()
    const { Wrapper } = createWrapper(mockClient)
    const { result } = renderHook(() => useTriggerBatchSync(), { wrapper: Wrapper })

    await act(async () => {
      result.current.mutate({ template_value: 'X', force: false, page_size: 100 })
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(mockClient.reporting.triggerBatchSync).toHaveBeenCalledWith(
      'X',
      { force: false, page_size: 100 },
    )
  })
})

describe('useTriggerTerminologySync', () => {
  it('calls triggerTerminologySync with namespace and pageSize', async () => {
    const mockClient = createMockClient()
    const { Wrapper } = createWrapper(mockClient)
    const { result } = renderHook(() => useTriggerTerminologySync(), { wrapper: Wrapper })

    await act(async () => {
      result.current.mutate({ namespace: 'kb', pageSize: 100 })
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(mockClient.reporting.triggerTerminologySync).toHaveBeenCalledWith('kb', 100)
  })
})

describe('useTriggerTermSync', () => {
  it('calls triggerTermSync with namespace and pageSize', async () => {
    const mockClient = createMockClient()
    const { Wrapper } = createWrapper(mockClient)
    const { result } = renderHook(() => useTriggerTermSync(), { wrapper: Wrapper })

    await act(async () => {
      result.current.mutate({ namespace: 'kb' })
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(mockClient.reporting.triggerTermSync).toHaveBeenCalledWith('kb', undefined)
  })
})

describe('useTriggerTermRelationSync', () => {
  it('calls triggerTermRelationSync with namespace and pageSize', async () => {
    const mockClient = createMockClient()
    const { Wrapper } = createWrapper(mockClient)
    const { result } = renderHook(() => useTriggerTermRelationSync(), { wrapper: Wrapper })

    await act(async () => {
      result.current.mutate({ namespace: 'kb', pageSize: 200 })
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(mockClient.reporting.triggerTermRelationSync).toHaveBeenCalledWith('kb', 200)
  })
})

describe('useCancelBatchJob', () => {
  it('calls cancelBatchJob and invalidates batch-jobs', async () => {
    const mockClient = createMockClient()
    const { Wrapper, queryClient } = createWrapper(mockClient)
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')

    const { result } = renderHook(() => useCancelBatchJob(), { wrapper: Wrapper })
    await act(async () => {
      result.current.mutate('job-1')
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(mockClient.reporting.cancelBatchJob).toHaveBeenCalledWith('job-1')
    expect(invalidateSpy).toHaveBeenCalled()
  })
})

describe('useClearCompletedJobs', () => {
  it('calls clearCompletedJobs and invalidates batch-jobs', async () => {
    const mockClient = createMockClient()
    const { Wrapper, queryClient } = createWrapper(mockClient)
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')

    const { result } = renderHook(() => useClearCompletedJobs(), { wrapper: Wrapper })
    await act(async () => {
      result.current.mutate()
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(mockClient.reporting.clearCompletedJobs).toHaveBeenCalled()
    expect(invalidateSpy).toHaveBeenCalled()
  })
})

// ===========================================================================
// Additional mutations in use-mutations.ts that hooks.test.tsx skipped
// ===========================================================================
//
// hooks.test.tsx covers the high-traffic create/delete hooks; these tests
// fill in the update/template/file-metadata/namespace/synonym surface so
// use-mutations.ts coverage rises from ~39% to clear CASE-341's 65% gate.

describe('useUpdateTerminology', () => {
  it('calls defStore.updateTerminology with id + data and invalidates terminologies', async () => {
    const mockClient = createMockClient()
    const { Wrapper, queryClient } = createWrapper(mockClient)
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')

    const { result } = renderHook(() => useUpdateTerminology(), { wrapper: Wrapper })
    await act(async () => {
      result.current.mutate({ id: 't1', data: { label: 'updated' } })
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(mockClient.defStore.updateTerminology).toHaveBeenCalledWith('t1', { label: 'updated' })
    expect(invalidateSpy).toHaveBeenCalled()
  })
})

describe('useUpdateTerm', () => {
  it('calls defStore.updateTerm with termId + data', async () => {
    const mockClient = createMockClient()
    const { Wrapper } = createWrapper(mockClient)
    const { result } = renderHook(() => useUpdateTerm(), { wrapper: Wrapper })

    await act(async () => {
      result.current.mutate({ termId: 'term1', data: { label: 'New' } })
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(mockClient.defStore.updateTerm).toHaveBeenCalledWith('term1', { label: 'New' })
  })
})

describe('useCreateTemplate', () => {
  it('calls templates.createTemplate and invalidates templates', async () => {
    const mockClient = createMockClient()
    const { Wrapper, queryClient } = createWrapper(mockClient)
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')

    const { result } = renderHook(() => useCreateTemplate(), { wrapper: Wrapper })
    const data = { value: 'NEW_TPL', label: 'New', fields: [] }
    await act(async () => {
      result.current.mutate(data)
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(mockClient.templates.createTemplate).toHaveBeenCalledWith(data)
    expect(invalidateSpy).toHaveBeenCalled()
  })
})

describe('useUpdateTemplate', () => {
  it('calls templates.updateTemplate with id + data', async () => {
    const mockClient = createMockClient()
    const { Wrapper } = createWrapper(mockClient)
    const { result } = renderHook(() => useUpdateTemplate(), { wrapper: Wrapper })

    await act(async () => {
      result.current.mutate({ id: 'tpl1', data: { label: 'updated' } })
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(mockClient.templates.updateTemplate).toHaveBeenCalledWith('tpl1', { label: 'updated' })
  })
})

describe('useDeleteTemplate', () => {
  it('calls templates.deleteTemplate with id and options', async () => {
    const mockClient = createMockClient()
    const { Wrapper } = createWrapper(mockClient)
    const { result } = renderHook(() => useDeleteTemplate(), { wrapper: Wrapper })

    await act(async () => {
      result.current.mutate({ id: 'tpl1', force: true, hardDelete: false })
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(mockClient.templates.deleteTemplate).toHaveBeenCalledWith('tpl1', {
      force: true,
      hardDelete: false,
    })
  })
})

describe('useActivateTemplate', () => {
  it('calls templates.activateTemplate with id + namespace', async () => {
    const mockClient = createMockClient()
    const { Wrapper } = createWrapper(mockClient)
    const { result } = renderHook(() => useActivateTemplate(), { wrapper: Wrapper })

    await act(async () => {
      result.current.mutate({ id: 'tpl1', namespace: 'kb', dry_run: false })
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(mockClient.templates.activateTemplate).toHaveBeenCalledWith('tpl1', {
      namespace: 'kb',
      dry_run: false,
    })
  })
})

describe('useCreateDocuments', () => {
  it('calls documents.createDocuments with array', async () => {
    const mockClient = createMockClient()
    const { Wrapper, queryClient } = createWrapper(mockClient)
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')

    const { result } = renderHook(() => useCreateDocuments(), { wrapper: Wrapper })
    const docs = [{ template_value: 'X', data: { a: 1 } }, { template_value: 'X', data: { a: 2 } }]
    await act(async () => {
      result.current.mutate(docs)
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(mockClient.documents.createDocuments).toHaveBeenCalledWith(docs)
    expect(invalidateSpy).toHaveBeenCalled()
  })
})

describe('useArchiveDocument', () => {
  it('calls documents.archiveDocument with id and archivedBy', async () => {
    const mockClient = createMockClient()
    const { Wrapper } = createWrapper(mockClient)
    const { result } = renderHook(() => useArchiveDocument(), { wrapper: Wrapper })

    await act(async () => {
      result.current.mutate({ id: 'doc1', archivedBy: 'admin' })
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(mockClient.documents.archiveDocument).toHaveBeenCalledWith('doc1', 'admin')
  })
})

describe('useDeleteFile', () => {
  it('calls files.deleteFile with id and invalidates files', async () => {
    const mockClient = createMockClient()
    const { Wrapper, queryClient } = createWrapper(mockClient)
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')

    const { result } = renderHook(() => useDeleteFile(), { wrapper: Wrapper })
    await act(async () => {
      result.current.mutate('f1')
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(mockClient.files.deleteFile).toHaveBeenCalledWith('f1')
    expect(invalidateSpy).toHaveBeenCalled()
  })
})

describe('useDeleteFiles', () => {
  it('calls files.deleteFiles with id array', async () => {
    const mockClient = createMockClient()
    const { Wrapper } = createWrapper(mockClient)
    const { result } = renderHook(() => useDeleteFiles(), { wrapper: Wrapper })

    await act(async () => {
      result.current.mutate(['f1', 'f2', 'f3'])
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(mockClient.files.deleteFiles).toHaveBeenCalledWith(['f1', 'f2', 'f3'])
  })
})

describe('useUpdateFileMetadata', () => {
  it('calls files.updateMetadata with fileId + data', async () => {
    const mockClient = createMockClient()
    const { Wrapper } = createWrapper(mockClient)
    const { result } = renderHook(() => useUpdateFileMetadata(), { wrapper: Wrapper })

    await act(async () => {
      result.current.mutate({ fileId: 'f1', data: { display_name: 'New name' } })
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(mockClient.files.updateMetadata).toHaveBeenCalledWith('f1', { display_name: 'New name' })
  })
})

describe('useCreateNamespace', () => {
  it('calls registry.createNamespace and invalidates registry', async () => {
    const mockClient = createMockClient()
    const { Wrapper, queryClient } = createWrapper(mockClient)
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')

    const { result } = renderHook(() => useCreateNamespace(), { wrapper: Wrapper })
    const data = { prefix: 'new', description: 'New namespace' }
    await act(async () => {
      result.current.mutate(data)
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(mockClient.registry.createNamespace).toHaveBeenCalledWith(data)
    expect(invalidateSpy).toHaveBeenCalled()
  })
})

describe('useDeleteNamespace', () => {
  it('calls registry.deleteNamespace with prefix and deletedBy', async () => {
    const mockClient = createMockClient()
    const { Wrapper } = createWrapper(mockClient)
    const { result } = renderHook(() => useDeleteNamespace(), { wrapper: Wrapper })

    await act(async () => {
      result.current.mutate({ prefix: 'old-ns', deletedBy: 'admin' })
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(mockClient.registry.deleteNamespace).toHaveBeenCalledWith('old-ns', 'admin')
  })
})

describe('useAddSynonym', () => {
  it('calls registry.addSynonym with data and invalidates registry', async () => {
    const mockClient = createMockClient()
    const { Wrapper, queryClient } = createWrapper(mockClient)
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')

    const { result } = renderHook(() => useAddSynonym(), { wrapper: Wrapper })
    const data = { canonical_id: 'r1', synonym: { erp_id: 'SAP-001' } }
    await act(async () => {
      result.current.mutate(data)
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(mockClient.registry.addSynonym).toHaveBeenCalledWith(data)
    expect(invalidateSpy).toHaveBeenCalled()
  })
})
