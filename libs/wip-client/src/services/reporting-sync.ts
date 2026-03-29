import { BaseService } from './base.js'
import type {
  IntegrityCheckResult,
  SearchResponse,
  ActivityResponse,
  TermDocumentsResponse,
  EntityReferencesResponse,
  ReferencedByResponse,
  ReportQueryParams,
  ReportQueryResult,
  ReportTable,
  ReportTableSchema,
  SyncStatus,
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
    namespace?: string
    status?: string
    limit?: number
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
