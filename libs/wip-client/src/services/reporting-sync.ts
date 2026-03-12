import { BaseService } from './base.js'
import type {
  IntegrityCheckResult,
  SearchResponse,
  ActivityResponse,
  TermDocumentsResponse,
  EntityReferencesResponse,
  ReferencedByResponse,
} from '../types/reporting.js'

export class ReportingSyncService extends BaseService {
  constructor(transport: import('../http.js').FetchTransport) {
    super(transport, '/api/reporting-sync')
  }

  async healthCheck(): Promise<boolean> {
    try {
      // The /health endpoint is at root, not under /api/reporting-sync/
      await this.transport.request('GET', '/health')
      return true
    } catch {
      return false
    }
  }

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

  async search(params: {
    query: string
    types?: string[]
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
