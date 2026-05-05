import { BaseService } from './base.js'
import { WipError } from '../errors.js'
import type {
  ActivityResponse,
  BatchEntitySyncResult,
  BatchJobCancelResult,
  BatchJobsCleared,
  BatchSyncJob,
  BatchSyncResponse,
  EntityReferencesResponse,
  IntegrityCheckResult,
  ReferencedByResponse,
  ReportQueryParams,
  ReportQueryResult,
  ReportTable,
  ReportTableSchema,
  SearchResponse,
  SyncStatus,
  TermDocumentsResponse,
} from '../types/reporting.js'

export class ReportingSyncService extends BaseService {
  constructor(transport: import('../http.js').FetchTransport) {
    super(transport, '/api/reporting-sync')
  }

  // ── Health & Status ──

  async healthCheck(): Promise<boolean> {
    try {
      // The /health endpoint is at root, not under /api/reporting-sync/
      await this.transport.request('GET', '/health')
      return true
    } catch {
      return false
    }
  }

  async getSyncStatus(): Promise<SyncStatus> {
    return this.get('/status')
  }

  // ── SQL Query Execution ──

  /** Execute a read-only SQL query against the PostgreSQL reporting database */
  async runQuery(
    sql: string,
    params?: unknown[],
    options?: { timeout_seconds?: number; max_rows?: number },
  ): Promise<ReportQueryResult> {
    const body: ReportQueryParams = {
      sql,
      params: params || [],
      ...options,
    }
    return this.post('/query', body)
  }

  // ── Batch Sync (CASE-283) ──

  /**
   * Trigger a batch sync for ALL templates with `sync_enabled=true`.
   * Returns one BatchSyncResponse per template; jobs run async on
   * the server. Poll `listBatchJobs()` or `getBatchJob(job_id)` for
   * progress.
   */
  async triggerBatchSyncAll(options?: {
    force?: boolean
    page_size?: number
  }): Promise<BatchSyncResponse[]> {
    return this.post('/sync/batch', undefined, { ...options })
  }

  /**
   * Trigger a batch sync for a single template (by value).
   * Job runs async; poll `getBatchJob(job_id)` for progress.
   */
  async triggerBatchSync(
    templateValue: string,
    options?: { force?: boolean; page_size?: number },
  ): Promise<BatchSyncResponse> {
    return this.post(`/sync/batch/${templateValue}`, undefined, { ...options })
  }

  /**
   * Synchronous batch sync for the terminologies entity table.
   * Returns the result inline; no per-job polling.
   */
  async triggerTerminologySync(
    namespace: string,
    pageSize: number = 100,
  ): Promise<BatchEntitySyncResult> {
    return this.post('/sync/batch/terminologies', undefined, {
      namespace, page_size: pageSize,
    })
  }

  /**
   * Synchronous batch sync for the terms entity table.
   * Iterates every active terminology in `namespace` and syncs its
   * terms.
   */
  async triggerTermSync(
    namespace: string,
    pageSize: number = 100,
  ): Promise<BatchEntitySyncResult> {
    return this.post('/sync/batch/terms', undefined, {
      namespace, page_size: pageSize,
    })
  }

  /**
   * Synchronous batch sync for the term_relations entity table.
   */
  async triggerTermRelationSync(
    namespace: string,
    pageSize: number = 100,
  ): Promise<BatchEntitySyncResult> {
    return this.post('/sync/batch/term_relations', undefined, {
      namespace, page_size: pageSize,
    })
  }

  /** List all batch sync jobs (in-memory, lost on reporting-sync restart). */
  async listBatchJobs(): Promise<BatchSyncJob[]> {
    return this.get('/sync/batch/jobs')
  }

  /** Fetch a single batch sync job by id. 404 if unknown. */
  async getBatchJob(jobId: string): Promise<BatchSyncJob> {
    return this.get(`/sync/batch/jobs/${jobId}`)
  }

  /** Cancel a running batch sync job. */
  async cancelBatchJob(jobId: string): Promise<BatchJobCancelResult> {
    return this.del(`/sync/batch/jobs/${jobId}`)
  }

  /** Clear all completed/failed/cancelled jobs from in-memory state. */
  async clearCompletedJobs(): Promise<BatchJobsCleared> {
    return this.del('/sync/batch/jobs')
  }

  // ── Sync Awareness ──

  /**
   * Wait for the reporting sync to catch up.
   *
   * Simple form — waits until at least one new event is processed:
   *   await client.reporting.awaitSync()
   *
   * Query form — waits until a specific row exists in PostgreSQL:
   *   await client.reporting.awaitSync({
   *     query: "SELECT 1 FROM dnd_monster WHERE document_id = $1",
   *     params: [docId],
   *   })
   */
  async awaitSync(options?: {
    /** SQL query that should return rows when sync is complete */
    query?: string
    /** Parameters for the SQL query */
    params?: unknown[]
    /** Timeout in milliseconds (default: 5000) */
    timeout?: number
    /** Poll interval in milliseconds (default: 200) */
    interval?: number
  }): Promise<void> {
    const timeout = options?.timeout ?? 5000
    const interval = options?.interval ?? 200
    const deadline = Date.now() + timeout

    if (options?.query) {
      // Query-based: poll until the expected row(s) appear
      while (Date.now() < deadline) {
        const result = await this.runQuery(options.query, options.params, { max_rows: 1 })
        if (result.row_count > 0) return
        await new Promise((resolve) => setTimeout(resolve, interval))
      }
      throw new WipError('Sync timeout: expected data not found in PostgreSQL')
    }

    // Event-count based: wait until at least one new event processes
    const before = await this.getSyncStatus()
    while (Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, interval))
      const current = await this.getSyncStatus()
      if (current.events_processed > before.events_processed) return
    }
    throw new WipError('Sync timeout: no new events processed')
  }

  // ── Table Introspection ──

  /** List all PostgreSQL reporting tables */
  async listTables(tableName?: string): Promise<{ tables: ReportTable[] }> {
    return this.get('/tables', tableName ? { table_name: tableName } : undefined)
  }

  /** Get PostgreSQL schema for a template's reporting table */
  async getTableSchema(templateValue: string): Promise<ReportTableSchema> {
    return this.get(`/schema/${templateValue}`)
  }

  // ── Integrity ──

  async getIntegrityCheck(params?: {
    template_status?: string
    document_status?: string
    template_limit?: number
    document_limit?: number
    check_term_refs?: boolean
    recent_first?: boolean
  }): Promise<IntegrityCheckResult> {
    return this.get('/health/integrity', params)
  }

  // ── Search & Activity ──

  async search(params: {
    query: string
    types?: string[]
    /**
     * Filter by namespace. Optional — when omitted the server runs
     * the search across all namespaces visible to the API key.
     * Single-namespace keys derive it implicitly; multi-namespace
     * keys see all of theirs.
     */
    namespace?: string
    status?: string
    limit?: number
    /** Restrict document search to a single template (by value). */
    template?: string
    /**
     * Document-search strategy. 'auto' (default) picks FTS for tables
     * with full_text_indexed fields and falls back to ILIKE elsewhere.
     * 'fts' forces FTS (skips tables without indexed fields). 'substring'
     * forces ILIKE on all tables.
     */
    mode?: 'auto' | 'fts' | 'substring'
    /**
     * When false (default), only active documents are returned —
     * aligns with PoNIF #1 "inactive means retired, not deleted".
     */
    include_inactive?: boolean
    /**
     * Snippet rendering for FTS hits. 'html' (default) wraps matched
     * terms with <b>...</b>. 'text' returns plain text.
     */
    snippet_format?: 'html' | 'text'
  }): Promise<SearchResponse> {
    return this.post('/search', params)
  }

  async getRecentActivity(params?: {
    types?: string
    limit?: number
  }): Promise<ActivityResponse> {
    return this.get('/activity/recent', params)
  }

  // ── References ──

  async getTermDocuments(termId: string, limit?: number): Promise<TermDocumentsResponse> {
    return this.get(`/references/term/${termId}/documents`, limit ? { limit } : undefined)
  }

  async getEntityReferences(
    entityType: 'document' | 'template' | 'terminology' | 'term' | 'file',
    entityId: string,
  ): Promise<EntityReferencesResponse> {
    return this.get(`/entity/${entityType}/${entityId}/references`)
  }

  async getReferencedBy(
    entityType: 'document' | 'template' | 'terminology' | 'term' | 'file',
    entityId: string,
    limit: number = 100,
  ): Promise<ReferencedByResponse> {
    return this.get(`/entity/${entityType}/${entityId}/referenced-by`, { limit })
  }
}
