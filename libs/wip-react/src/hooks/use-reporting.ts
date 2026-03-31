import { useQuery } from '@tanstack/react-query'
import type { UseQueryOptions, QueryKey } from '@tanstack/react-query'
import type { IntegrityCheckResult, ActivityResponse, ReportQueryResult } from '@wip/client'
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
