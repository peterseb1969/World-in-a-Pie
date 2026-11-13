import { BaseService } from './base.js'
import {
  WipAuthError,
  WipError,
  WipNetworkError,
  WipServerError,
  WipValidationError,
} from '../errors.js'
import type {
  ActivityResponse,
  BatchEntitySyncResult,
  BatchJobCancelResult,
  BatchJobsCleared,
  BatchSyncJob,
  BatchSyncResponse,
  DeleteViewResult,
  EntityReferencesResponse,
  IntegrityCheckResult,
  ListViewsResponse,
  NamespaceParityResult,
  ReferencedByResponse,
  RegisterViewResult,
  ReportEntity,
  ReportQueryParams,
  ReportQueryResult,
  ReportTable,
  ReportTableSchema,
  SearchResponse,
  SyncStatus,
  TermDocumentsResponse,
  ViewRowsParams,
} from '../types/reporting.js'

/**
 * Errors a sync wait outlives: the table is created by the first synced
 * document, and a 5xx or a dropped connection may pass. An SQL mistake or a
 * refused request will not, so it is thrown at once.
 */
function isTransientSyncError(err: unknown): err is WipError {
  if (err instanceof WipNetworkError || err instanceof WipServerError) return true
  return err instanceof WipValidationError && /does not exist/.test(err.message)
}

export class ReportingSyncService extends BaseService {
  constructor(transport: import('../http.js').FetchTransport) {
    super(transport, '/api/reporting-sync')
  }

  // ── Health & Status ──

  /**
   * True only when reporting-sync reports `healthy`. `degraded` (PostgreSQL or
   * NATS down) and `unhealthy` are false, as is an unreachable service. An
   * authentication failure is thrown, not read as "down".
   *
   * Uses the routed `/api/reporting-sync/health` (no key needed): the bare
   * `/health` exists only on the service's own port, so through Caddy, the
   * router or @wip/proxy it is refused and a healthy service read as down.
   */
  async healthCheck(): Promise<boolean> {
    try {
      const health = await this.get<{ status?: string }>('/health')
      return health?.status === 'healthy'
    } catch (err) {
      if (err instanceof WipAuthError) throw err
      return false
    }
  }

  async getSyncStatus(): Promise<SyncStatus> {
    return this.get('/status')
  }

  // ── SQL Query Execution ──

  /**
   * Execute a read-only SQL query against the PostgreSQL reporting database.
   *
   * Reporting tables live in per-namespace PostgreSQL schemas
   * (`"<ns>"."doc_<value>"`). Pass `namespace` so unqualified table names
   * resolve in that namespace's schema, or schema-qualify each table in the
   * SQL for cross-namespace queries.
   */
  async runQuery(
    sql: string,
    params?: unknown[],
    options?: { timeout_seconds?: number; max_rows?: number; namespace?: string },
  ): Promise<ReportQueryResult> {
    const body: ReportQueryParams = {
      sql,
      params: params || [],
      ...options,
    }
    return this.post('/query', body)
  }

  // ── Registered Views ──

  /**
   * Register (or replace) a named SQL view in the namespace's reporting
   * schema. Idempotent and bootstrap-friendly: re-registering the same
   * name replaces the definition (DROP + CREATE server-side, so the
   * column set may change); `created` distinguishes first registration
   * from replace. Register on every bootstrap run — that is the
   * authoritative recovery after any reporting reset.
   *
   * Two rules that bite first (full manual: docs/reporting-views.md):
   * - Unqualified table names resolve in the NAMESPACE's schema
   *   (`doc_<value>`, `term_relations`, ...); other schemas only when
   *   qualified — the same posture as runQuery with `namespace`.
   * - The SQL is validated against the live schema, so a view can only
   *   register after its source templates have synced at least once.
   *   Bootstrap order: namespace → templates → first sync → views.
   *
   * The SQL must be a single read-only SELECT; platform relation names
   * (`doc_*`, `*__v<N>`, `*__entities`, metadata tables, `_wip_*`) are
   * reserved — use a distinct name, e.g. a `v_` prefix.
   *
   * Authorization: a key with ADMIN on the namespace registers views
   * CONTAINED in the namespace's own schema (a dependency on any other
   * schema is refused with 403 and rolled back); wip-admins / wip-services
   * keys may register cross-schema views deliberately.
   */
  async registerView(
    namespace: string,
    view: { name: string; sql: string; registered_by?: string },
  ): Promise<RegisterViewResult> {
    return this.post(`/namespace/${namespace}/views`, view)
  }

  /** List the namespace's registered views (name, sql, registration audit). */
  async listViews(namespace: string): Promise<ListViewsResponse> {
    return this.get(`/namespace/${namespace}/views`)
  }

  /**
   * Read a registered view's rows with structured filters (requires READ on
   * the namespace). No SQL: filters are (column, op, value)
   * triples validated against the view's actual columns and bound as
   * parameters; `limit`/`offset` page through; the result is shaped exactly
   * like runQuery's (columns, rows as objects keyed by column, row_count,
   * truncated). Only registered views answer here — a plain reporting
   * table is a 404.
   */
  async readViewRows(
    namespace: string,
    name: string,
    params?: ViewRowsParams,
  ): Promise<ReportQueryResult> {
    return this.post(`/namespace/${namespace}/views/${name}/rows`, params ?? {})
  }

  /**
   * Drop a registered view and its registration, atomically. A view
   * another registered view depends on answers 409 — delete dependents
   * first. A later re-register of the same name is a fresh create.
   */
  async deleteView(namespace: string, name: string): Promise<DeleteViewResult> {
    return this.del(`/namespace/${namespace}/views/${name}`)
  }

  // ── Batch Sync (CASE-283) ──

  /**
   * Trigger a batch sync for ALL templates with `sync_enabled=true`.
   * Returns one BatchSyncResponse per template; jobs run async on
   * the server. Poll `listBatchJobs()` or `getBatchJob(job_id)` for
   * progress.
   *
   * `namespace` scopes every job to that namespace's documents (the
   * template list stays instance-wide — documents may be based on
   * templates owned by other namespaces). A template whose sync is
   * already active returns its existing job instead of stacking a
   * duplicate; the trigger is idempotent and acknowledges promptly.
   *
   * `force` drops each in-scope template's existing reporting relations
   * (version tables, entity views, any legacy pre-split table) before
   * its sync — rebuild from source, the recovery path for mis-shaped
   * DDL that upserts cannot heal. Requires `namespace` (400 without
   * it). Mid-rebuild, SQL readers see relation-does-not-exist for the
   * affected templates. An already-active sync is NOT force-rebuilt —
   * its per-item message says so; cancel the job and re-trigger.
   */
  async triggerBatchSyncAll(options?: {
    force?: boolean
    page_size?: number
    namespace?: string
  }): Promise<BatchSyncResponse[]> {
    return this.post('/sync/batch', undefined, { ...options })
  }

  /**
   * Trigger a batch sync for a single template (by value).
   * Job runs async; poll `getBatchJob(job_id)` for progress.
   *
   * `namespace` disambiguates the template lookup (a value is unique
   * only within a namespace) AND scopes the sync to that namespace's
   * documents. If an overlapping sync is already active, the existing
   * job is returned instead of a duplicate.
   *
   * `force` drops the template's existing reporting relations in the
   * target namespace before syncing — rebuild from source. Requires
   * `namespace` (400 without it). If an overlapping sync is active,
   * force is NOT applied (the response message says so); cancel the
   * job and re-trigger.
   */
  async triggerBatchSync(
    templateValue: string,
    options?: { force?: boolean; page_size?: number; namespace?: string },
  ): Promise<BatchSyncResponse> {
    // The /template/ path reaches every value, including the ones the
    // definition syncs own (terms, terminologies, templates, term_relations).
    return this.post(
      `/sync/batch/template/${encodeURIComponent(templateValue)}`,
      undefined,
      { ...options },
    )
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
   * Wait until a query over the reporting tables returns a row — the way to
   * know that a specific write has reached PostgreSQL:
   *
   *   await client.reporting.awaitSync({
   *     query: 'SELECT 1 FROM doc_monster WHERE document_id = $1',
   *     params: [docId],
   *     namespace: 'dnd',
   *   })
   *
   * Polls until the deadline. A table not created yet, a 5xx and a network
   * failure are retried; any other error (an SQL mistake, a refused
   * namespace) is thrown at once. On timeout the last retried error, if any,
   * is in the thrown error's `detail`.
   *
   * There is no form without a query: reporting-sync counts processed events
   * across every namespace, so no counter can say that one write has synced.
   */
  async awaitSync(options: {
    /** SQL that returns a row once the sync is complete. */
    query: string
    /** Parameters for the query. */
    params?: unknown[]
    /** The namespace whose reporting schema the query reads; omittable under a single-namespace key. */
    namespace?: string
    /** Timeout in milliseconds (default: 5000). */
    timeout?: number
    /** Poll interval in milliseconds (default: 200). */
    interval?: number
  }): Promise<void> {
    const timeout = options.timeout ?? 5000
    const interval = options.interval ?? 200
    const deadline = Date.now() + timeout
    let lastError: WipError | undefined
    while (Date.now() < deadline) {
      try {
        const result = await this.runQuery(options.query, options.params, {
          max_rows: 1,
          ...(options.namespace ? { namespace: options.namespace } : {}),
        })
        if (result.row_count > 0) return
      } catch (err) {
        if (!isTransientSyncError(err)) throw err
        lastError = err
      }
      await new Promise((resolve) => setTimeout(resolve, interval))
    }
    throw new WipError(
      lastError
        ? `Sync timeout: the query never succeeded (last error: ${lastError.message})`
        : 'Sync timeout: expected data not found in PostgreSQL',
      undefined,
      lastError,
    )
  }

  // ── Table Introspection ──

  /**
   * List reporting relations, grouped entity-first. Without `tableName`,
   * the response carries `entities` (one entry per template with its
   * version tables and views — the shape UIs should render) alongside the
   * flat `tables` list. With `tableName`, returns that single relation
   * with full column detail (works for views and version tables alike),
   * and `entities` is omitted.
   */
  async listTables(
    tableName?: string,
    namespace?: string,
  ): Promise<{ tables: ReportTable[]; entities?: ReportEntity[] }> {
    const params: Record<string, string> = {}
    if (tableName) params.table_name = tableName
    if (namespace) params.namespace = namespace
    return this.get('/tables', Object.keys(params).length ? params : undefined)
  }

  /**
   * Get PostgreSQL columns for a template's reporting relation.
   * `namespace` is required by the endpoint (the relation lives in that
   * namespace's schema). Without `version`: the bare-name entity view
   * (the default query surface). With `version`: that version's physical
   * table shape.
   */
  async getTableSchema(
    templateValue: string,
    namespace: string,
    version?: number,
  ): Promise<ReportTableSchema> {
    const params: Record<string, string> = { namespace }
    if (version !== undefined) params.version = String(version)
    return this.get(`/schema/${templateValue}`, params)
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

  /**
   * Namespace parity: per sync-enabled template, is the reporting table
   * present with the expected columns, and (by default) do its row counts
   * match the active documents? Pass `includeCounts: false` for the cheap
   * structure-only form.
   */
  async getParity(
    namespace: string,
    options?: { includeCounts?: boolean },
  ): Promise<NamespaceParityResult> {
    const params: Record<string, string> = { namespace }
    if (options?.includeCounts !== undefined) params.include_counts = String(options.includeCounts)
    return this.get('/parity', params)
  }

  // ── CSV Export ──

  /**
   * Export a whole reporting table (`doc_*` or a metadata table such as
   * `terminologies`) from the namespace's schema as CSV. A table that does
   * not exist is a 404. Not to be confused with `documents.exportTableCsv`,
   * document-store's table-view CSV of one template.
   */
  async exportTableCsv(
    table: string,
    namespace: string,
    options?: { filename?: string; timeoutSeconds?: number },
  ): Promise<Blob> {
    const params: Record<string, string> = { table, namespace }
    if (options?.filename) params.filename = options.filename
    if (options?.timeoutSeconds !== undefined) params.timeout_seconds = String(options.timeoutSeconds)
    return this.getBlob('/export/csv', params)
  }

  /**
   * Export a read-only SQL query's result as CSV. With `namespace`,
   * unqualified table names resolve in that namespace's schema (as in
   * `runQuery`); omit it and schema-qualify for cross-namespace SQL. An
   * invalid statement or unknown relation is a 400, never an empty file.
   * Any key may call it: a platform admin or service key reads every schema,
   * any other key only the namespaces it can read (as with runQuery).
   */
  async exportQueryCsv(
    sql: string,
    options?: {
      params?: unknown[]
      namespace?: string
      timeoutSeconds?: number
      filename?: string
    },
  ): Promise<Blob> {
    const body: Record<string, unknown> = { sql, params: options?.params ?? [] }
    if (options?.namespace) body.namespace = options.namespace
    if (options?.timeoutSeconds !== undefined) body.timeout_seconds = options.timeoutSeconds
    if (options?.filename) body.filename = options.filename
    return this.postBlob('/export/csv', body)
  }

  // ── Search & Activity ──

  /**
   * Unified search with per-type pagination (CASE-329).
   *
   * Breaking change in @wip/client 0.19.0: the response shape moved
   * from a flat `results: SearchResult[]` to per-type buckets keyed
   * by entity type, each with its own pagination envelope. Same
   * `page`/`page_size` applies to every type. The legacy `limit`
   * parameter is still accepted as a deprecation-window alias for
   * `page_size` — use `page_size` going forward.
   */
  async search(params: {
    query: string
    types?: string[]
    /**
     * The namespace to search. Omittable for a single-namespace key (it is
     * derived) and for a platform admin (every readable namespace is
     * searched). A key that can read several namespaces, without the admin
     * group, must name one: the service answers 400 otherwise.
     */
    namespace?: string
    status?: string
    /** Page number (1-indexed). Default 1. (CASE-329) */
    page?: number
    /** Items per type. Default 50, cap 100. (CASE-329) */
    page_size?: number
    /** DEPRECATED (CASE-329): alias for page_size when page=1. */
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

  /** `namespace` is required for a key that can read several namespaces (400 otherwise). */
  async getRecentActivity(params?: {
    types?: string
    limit?: number
    namespace?: string
  }): Promise<ActivityResponse> {
    return this.get('/activity/recent', params)
  }

  // ── References ──

  /** `namespace` is required for a key that can read several namespaces (400 otherwise). */
  async getTermDocuments(termId: string, limit?: number, namespace?: string): Promise<TermDocumentsResponse> {
    return this.get(`/references/term/${termId}/documents`, {
      ...(limit ? { limit } : {}),
      ...(namespace ? { namespace } : {}),
    })
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
    namespace?: string,
  ): Promise<ReferencedByResponse> {
    // `namespace` is required for a key that can read several namespaces (400 otherwise).
    return this.get(`/entity/${entityType}/${entityId}/referenced-by`, {
      limit,
      ...(namespace ? { namespace } : {}),
    })
  }
}
