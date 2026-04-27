import { BaseService } from './base.js'
import { WipBulkItemError } from '../errors.js'
import type { BulkResponse, BulkResultItem } from '../types/common.js'
import type {
  Terminology,
  TerminologyListResponse,
  CreateTerminologyRequest,
  UpdateTerminologyRequest,
  Term,
  TermListResponse,
  CreateTermRequest,
  UpdateTermRequest,
  DeprecateTermRequest,
  ImportTerminologyRequest,
  ExportTerminologyResponse,
  ValidateValueRequest,
  ValidateValueResponse,
  BulkValidateRequest,
  BulkValidateResponse,
  AuditLogResponse,
} from '../types/terminology.js'
import type {
  TermRelation,
  TermRelationListResponse,
  CreateTermRelationRequest,
  DeleteTermRelationRequest,
  TraversalResponse,
} from '../types/ontology.js'

export class DefStoreService extends BaseService {
  constructor(transport: import('../http.js').FetchTransport) {
    super(transport, '/api/def-store')
  }

  // ---- Terminologies ----

  async listTerminologies(params?: {
    page?: number
    page_size?: number
    status?: string
    value?: string
    namespace?: string
  }): Promise<TerminologyListResponse> {
    return this.get('/terminologies', params)
  }

  async getTerminology(id: string): Promise<Terminology> {
    return this.get(`/terminologies/${id}`)
  }

  async createTerminology(data: CreateTerminologyRequest): Promise<BulkResultItem> {
    return this.bulkWriteOne('/terminologies', data)
  }

  async createTerminologies(data: CreateTerminologyRequest[]): Promise<BulkResponse> {
    return this.bulkWrite('/terminologies', data)
  }

  async updateTerminology(id: string, data: UpdateTerminologyRequest): Promise<BulkResultItem> {
    return this.bulkWriteOne('/terminologies', { ...data, terminology_id: id }, 'PUT')
  }

  async deleteTerminology(id: string, options?: {
    force?: boolean
    hardDelete?: boolean
  }): Promise<BulkResultItem> {
    return this.bulkWriteOne('/terminologies', {
      id,
      force: options?.force,
      hard_delete: options?.hardDelete,
    }, 'DELETE')
  }

  // ---- Terms ----

  async listTerms(terminologyId: string, params?: {
    page?: number
    page_size?: number
    status?: string
    search?: string
    namespace?: string
  }): Promise<TermListResponse> {
    return this.get(`/terminologies/${terminologyId}/terms`, params)
  }

  async getTerm(termId: string): Promise<Term> {
    return this.get(`/terms/${termId}`)
  }

  async createTerm(
    terminologyId: string,
    data: CreateTermRequest,
    options: { namespace: string },
  ): Promise<BulkResultItem> {
    const resp = await this.post<BulkResponse>(
      `/terminologies/${terminologyId}/terms`,
      [data],
      { namespace: options.namespace },
    )
    const result = resp.results[0]
    if (result.status === 'error') {
      throw new WipBulkItemError(
        result.error || 'Operation failed',
        result.index,
        result.status,
      )
    }
    return result
  }

  async createTerms(
    terminologyId: string,
    terms: CreateTermRequest[],
    options: { namespace: string; batch_size?: number; registry_batch_size?: number },
  ): Promise<BulkResponse> {
    return this.post(`/terminologies/${terminologyId}/terms`, terms, options)
  }

  async updateTerm(termId: string, data: UpdateTermRequest): Promise<BulkResultItem> {
    return this.bulkWriteOne('/terms', { ...data, term_id: termId }, 'PUT')
  }

  async deprecateTerm(termId: string, data: DeprecateTermRequest): Promise<BulkResultItem> {
    return this.bulkWriteOne('/terms/deprecate', { ...data, term_id: termId })
  }

  async deleteTerm(termId: string, options?: { hardDelete?: boolean }): Promise<BulkResultItem> {
    return this.bulkWriteOne('/terms', {
      id: termId,
      hard_delete: options?.hardDelete,
    }, 'DELETE')
  }

  // ---- Import/Export ----

  async importTerminology(data: ImportTerminologyRequest): Promise<{
    terminology: Terminology
    terms_result: BulkResponse
    relationships_result?: { total: number; created: number; skipped: number; errors: number }
  }> {
    return this.post('/import-export/import', data)
  }

  async exportTerminology(
    terminologyId: string,
    options?: {
      format?: 'json' | 'csv'
      includeInactive?: boolean
      includeRelationships?: boolean
      includeMetadata?: boolean
      languages?: string[]
    },
  ): Promise<ExportTerminologyResponse | string> {
    return this.get(`/import-export/export/${terminologyId}`, {
      format: options?.format ?? 'json',
      include_inactive: options?.includeInactive,
      include_relationships: options?.includeRelationships,
      include_metadata: options?.includeMetadata,
      languages: options?.languages,
    })
  }

  async importOntology(
    data: Record<string, unknown>,
    options: {
      namespace: string
      terminology_value?: string
      terminology_label?: string
      prefix_filter?: string
      include_deprecated?: boolean
      max_synonyms?: number
      batch_size?: number
      registry_batch_size?: number
      relationship_batch_size?: number
      skip_duplicates?: boolean
      update_existing?: boolean
    },
  ): Promise<{
    terminology: { terminology_id: string; value: string; label: string; status: string }
    terms: { total: number; created: number; skipped: number; errors: number }
    relationships: {
      total: number; created: number; skipped: number; errors: number
      predicate_distribution: Record<string, number>
      error_samples?: string[]
    }
    elapsed_seconds: number
  }> {
    return this.post('/import-export/import-ontology', data, options)
  }

  // ---- Validation ----

  async validateValue(data: ValidateValueRequest): Promise<ValidateValueResponse> {
    return this.post('/validate', data)
  }

  async bulkValidate(data: BulkValidateRequest): Promise<BulkValidateResponse> {
    return this.post('/validate/bulk', data)
  }

  // ---- Ontology / Term Relations ----
  //
  // The platform renamed this surface in WIP commit 2eeb872 (Phase 0 of
  // the document-relationships work, 2026-04-25): "relationship" now
  // refers to document-to-document edges; "relation" / "term-relation"
  // refers to the term-ontology edges (is_a, part_of, ...). HTTP path,
  // wire field, and these client method names all moved together — no
  // backward-compat aliases.

  async listTermRelations(params: {
    term_id: string
    direction?: string
    relation_type?: string
    namespace?: string
    page?: number
    page_size?: number
  }): Promise<TermRelationListResponse> {
    return this.get('/ontology/term-relations', params)
  }

  async listAllTermRelations(params?: {
    namespace?: string
    relation_type?: string
    status?: string
    page?: number
    page_size?: number
  }): Promise<TermRelationListResponse> {
    return this.get('/ontology/term-relations/all', params)
  }

  async createTermRelations(items: CreateTermRelationRequest[], namespace: string): Promise<BulkResponse> {
    return this.post('/ontology/term-relations', items, { namespace })
  }

  async deleteTermRelations(items: DeleteTermRelationRequest[], namespace: string): Promise<BulkResponse> {
    return this.del('/ontology/term-relations', items, { namespace })
  }

  async getAncestors(termId: string, params?: {
    relation_type?: string
    namespace?: string
    max_depth?: number
  }): Promise<TraversalResponse> {
    return this.get(`/ontology/terms/${termId}/ancestors`, params)
  }

  async getDescendants(termId: string, params?: {
    relation_type?: string
    namespace?: string
    max_depth?: number
  }): Promise<TraversalResponse> {
    return this.get(`/ontology/terms/${termId}/descendants`, params)
  }

  async getParents(termId: string, namespace: string): Promise<TermRelation[]> {
    return this.get(`/ontology/terms/${termId}/parents`, { namespace })
  }

  async getChildren(termId: string, namespace: string): Promise<TermRelation[]> {
    return this.get(`/ontology/terms/${termId}/children`, { namespace })
  }

  // ---- Audit Log ----

  async getTerminologyAuditLog(terminologyId: string, params?: {
    action?: string
    page?: number
    page_size?: number
  }): Promise<AuditLogResponse> {
    return this.get(`/audit/terminologies/${terminologyId}`, params)
  }

  async getTermAuditLog(termId: string, params?: {
    action?: string
    page?: number
    page_size?: number
  }): Promise<AuditLogResponse> {
    return this.get(`/audit/terms/${termId}`, params)
  }

  async getRecentAuditLog(params?: {
    action?: string
    page?: number
    page_size?: number
  }): Promise<AuditLogResponse> {
    return this.get('/audit/', params)
  }
}
