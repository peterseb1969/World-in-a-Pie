import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { UseMutationOptions, UseQueryOptions, QueryKey } from '@tanstack/react-query'
import type {
  ActivityResponse,
  BatchEntitySyncResult,
  BatchJobCancelResult,
  BatchJobsCleared,
  BatchSyncJob,
  BatchSyncResponse,
  IntegrityCheckResult,
  ReportQueryResult,
  SyncStatus,
} from '@wip/client'
import { useWipClient } from '../provider.js'
import { wipKeys } from '../utils/keys.js'
import { STALE_TIMES } from '../utils/defaults.js'

const REPORT_QUERY_STALE_TIME = 10_000 // 10s — report data is eventually consistent

/**
 * Execute a read-only SQL query against the PostgreSQL reporting layer.
 *
 * Use this for cross-entity aggregations, counts, and analytics.
 * Report queries are eventually consistent — don't use for state management.
 * Use the document/template hooks for authoritative current state.
 *
 * @example
 * // Simple aggregation
 * const { data } = useReportQuery(
 *   'SELECT location, COUNT(*) as scenes FROM aa_event GROUP BY location'
 * )
 *
 * @example
 * // Parameterized query with custom cache key
 * const { data } = useReportQuery(
 *   'SELECT * FROM aa_event WHERE location = $1',
 *   ['BLACKWOOD_MANOR'],
 *   { queryKey: ['cross-refs', 'location', 'BLACKWOOD_MANOR'] }
 * )
 */
export function useReportQuery(
  sql: string,
  params?: unknown[],
  options?: Omit<UseQueryOptions<ReportQueryResult>, 'queryFn'> & {
    maxRows?: number
    timeoutSeconds?: number
  },
) {
  const client = useWipClient()
  const { maxRows, timeoutSeconds, ...queryOptions } = options ?? {}
  return useQuery<ReportQueryResult>({
    queryKey: wipKeys.reporting.query(sql, params),
    queryFn: () => client.reporting.runQuery(sql, params, {
      max_rows: maxRows,
      timeout_seconds: timeoutSeconds,
    }),
    staleTime: REPORT_QUERY_STALE_TIME,
    ...queryOptions,
  })
}

export function useIntegrityCheck(
  params?: {
    template_status?: string
    document_status?: string
    template_limit?: number
    document_limit?: number
    check_term_refs?: boolean
    recent_first?: boolean
  },
  options?: Omit<UseQueryOptions<IntegrityCheckResult>, 'queryKey' | 'queryFn'>,
) {
  const client = useWipClient()
  return useQuery({
    queryKey: wipKeys.reporting.integrity(params),
    queryFn: () => client.reporting.getIntegrityCheck(params),
    staleTime: STALE_TIMES.reporting,
    ...options,
  })
}

export function useActivity(
  params?: { types?: string; limit?: number },
  options?: Omit<UseQueryOptions<ActivityResponse>, 'queryKey' | 'queryFn'>,
) {
  const client = useWipClient()
  return useQuery({
    queryKey: wipKeys.reporting.activity(params),
    queryFn: () => client.reporting.getRecentActivity(params),
    staleTime: STALE_TIMES.reporting,
    ...options,
  })
}


// ────────────────────────────────────────────────────────────────────
// Batch sync (CASE-283)
//
// The reporting layer's catch-up surface — used by the Console's
// reporting/sync admin page. After events that empty or rebuild the
// reporting layer (fresh install, recreated reporting-sync, restore
// from backup) the batch endpoints repopulate doc_* tables from
// MongoDB.
// ────────────────────────────────────────────────────────────────────

/**
 * Sync service status — running, NATS/Postgres connections,
 * events_processed/failed, tables_managed. Useful as a standalone
 * dashboard widget AND as a signal for the "first-time setup" CTA
 * (when tables_managed=0 but documents exist).
 *
 * @example
 *   const { data } = useSyncStatus({ refetchInterval: 5000 })
 */
export function useSyncStatus(
  options?: Omit<UseQueryOptions<SyncStatus>, 'queryKey' | 'queryFn'>,
) {
  const client = useWipClient()
  return useQuery({
    queryKey: wipKeys.reporting.syncStatus(),
    queryFn: () => client.reporting.getSyncStatus(),
    staleTime: STALE_TIMES.reporting,
    ...options,
  })
}

/**
 * List all batch sync jobs. In-memory on the server — restarts of
 * reporting-sync clear the list. Pass `refetchInterval` for live
 * polling while jobs are in flight (CT_TRIAL_AE was 11min for 153k
 * docs; pick 2-5s as a reasonable default).
 *
 * @example
 *   const hasRunning = jobs?.some(j => j.status === 'running' || j.status === 'pending')
 *   const { data: jobs } = useBatchJobs({ refetchInterval: hasRunning ? 3000 : false })
 */
export function useBatchJobs(
  options?: Omit<UseQueryOptions<BatchSyncJob[]>, 'queryKey' | 'queryFn'>,
) {
  const client = useWipClient()
  return useQuery({
    queryKey: wipKeys.reporting.batchJobs(),
    queryFn: () => client.reporting.listBatchJobs(),
    staleTime: 0, // jobs are live state — always refetch
    ...options,
  })
}

/**
 * Single batch sync job by id. Pass `refetchInterval` for live
 * progress (`documents_synced` / `total_documents` / `current_page`).
 */
export function useBatchJob(
  jobId: string,
  options?: Omit<UseQueryOptions<BatchSyncJob>, 'queryKey' | 'queryFn'>,
) {
  const client = useWipClient()
  return useQuery({
    queryKey: wipKeys.reporting.batchJob(jobId),
    queryFn: () => client.reporting.getBatchJob(jobId),
    staleTime: 0,
    enabled: Boolean(jobId),
    ...options,
  })
}

type BatchSyncAllVars = { force?: boolean; page_size?: number } | void

/** Trigger a batch sync for all templates with sync_enabled=true. */
export function useTriggerBatchSyncAll(
  options?: Omit<UseMutationOptions<BatchSyncResponse[], Error, BatchSyncAllVars>, 'mutationFn'>,
) {
  const client = useWipClient()
  const queryClient = useQueryClient()
  const { onSuccess, ...rest } = options ?? {}
  return useMutation({
    ...rest,
    mutationFn: (vars) => client.reporting.triggerBatchSyncAll(vars ?? undefined),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.reporting.batchJobs() })
      onSuccess?.(...args)
    },
  })
}

type BatchSyncVars = {
  template_value: string
  force?: boolean
  page_size?: number
}

/** Trigger a batch sync for a specific template. */
export function useTriggerBatchSync(
  options?: Omit<UseMutationOptions<BatchSyncResponse, Error, BatchSyncVars>, 'mutationFn'>,
) {
  const client = useWipClient()
  const queryClient = useQueryClient()
  const { onSuccess, ...rest } = options ?? {}
  return useMutation({
    ...rest,
    mutationFn: ({ template_value, force, page_size }) =>
      client.reporting.triggerBatchSync(template_value, { force, page_size }),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.reporting.batchJobs() })
      onSuccess?.(...args)
    },
  })
}

type EntitySyncVars = { namespace: string; pageSize?: number }

/** Trigger a synchronous batch sync for the terminologies table. */
export function useTriggerTerminologySync(
  options?: Omit<UseMutationOptions<BatchEntitySyncResult, Error, EntitySyncVars>, 'mutationFn'>,
) {
  const client = useWipClient()
  return useMutation({
    ...options,
    mutationFn: ({ namespace, pageSize }) =>
      client.reporting.triggerTerminologySync(namespace, pageSize),
  })
}

/** Trigger a synchronous batch sync for the terms table. */
export function useTriggerTermSync(
  options?: Omit<UseMutationOptions<BatchEntitySyncResult, Error, EntitySyncVars>, 'mutationFn'>,
) {
  const client = useWipClient()
  return useMutation({
    ...options,
    mutationFn: ({ namespace, pageSize }) =>
      client.reporting.triggerTermSync(namespace, pageSize),
  })
}

/** Trigger a synchronous batch sync for the term_relations table. */
export function useTriggerTermRelationSync(
  options?: Omit<UseMutationOptions<BatchEntitySyncResult, Error, EntitySyncVars>, 'mutationFn'>,
) {
  const client = useWipClient()
  return useMutation({
    ...options,
    mutationFn: ({ namespace, pageSize }) =>
      client.reporting.triggerTermRelationSync(namespace, pageSize),
  })
}

/** Cancel a running batch sync job. */
export function useCancelBatchJob(
  options?: Omit<UseMutationOptions<BatchJobCancelResult, Error, string>, 'mutationFn'>,
) {
  const client = useWipClient()
  const queryClient = useQueryClient()
  const { onSuccess, ...rest } = options ?? {}
  return useMutation({
    ...rest,
    mutationFn: (jobId) => client.reporting.cancelBatchJob(jobId),
    onSuccess: (...args) => {
      const jobId = args[1]
      queryClient.invalidateQueries({ queryKey: wipKeys.reporting.batchJobs() })
      queryClient.invalidateQueries({ queryKey: wipKeys.reporting.batchJob(jobId) })
      onSuccess?.(...args)
    },
  })
}

/** Clear all completed/failed/cancelled jobs from in-memory state. */
export function useClearCompletedJobs(
  options?: Omit<UseMutationOptions<BatchJobsCleared, Error, void>, 'mutationFn'>,
) {
  const client = useWipClient()
  const queryClient = useQueryClient()
  const { onSuccess, ...rest } = options ?? {}
  return useMutation({
    ...rest,
    mutationFn: () => client.reporting.clearCompletedJobs(),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.reporting.batchJobs() })
      onSuccess?.(...args)
    },
  })
}
