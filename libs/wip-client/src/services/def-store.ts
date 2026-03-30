import { BaseService } from './base.js'
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
} from '../types/terminology.js'
import type {
  Relationship,
  RelationshipListResponse,
  CreateRelationshipRequest,
  DeleteRelationshipRequest,
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

  async createTerm(terminologyId: string, data: CreateTermRequest): Promise<BulkResultItem> {
    return this.bulkWriteOne(`/terminologies/${terminologyId}/terms`, data)
  }

  async createTerms(
    terminologyId: string,
    terms: CreateTermRequest[],
    options?: { batch_size?: number; registry_batch_size?: number },
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
    options?: {
      terminology_value?: string
      terminology_label?: string
      namespace?: string
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

  // ---- Ontology / Relationships ----

  async listRelationships(params: {
    term_id: string
    direction?: string
    relationship_type?: string
    namespace?: string
    page?: number
    page_size?: number
  }): Promise<RelationshipListResponse> {
    return this.get('/ontology/relationships', params)
  }

  async listAllRelationships(params?: {
    namespace?: string
    relationship_type?: string
    status?: string
    page?: number
    page_size?: number
  }): Promise<RelationshipListResponse> {
    return this.get('/ontology/relationships/all', params)
  }

  async createRelationships(items: CreateRelationshipRequest[], namespace: string): Promise<BulkResponse> {
    return this.post('/ontology/relationships', items, { namespace })
  }

  async deleteRelationships(items: DeleteRelationshipRequest[], namespace: string): Promise<BulkResponse> {
    return this.del('/ontology/relationships', items, { namespace })
  }

  async getAncestors(termId: string, params?: {
    relationship_type?: string
    namespace?: string
    max_depth?: number
  }): Promise<TraversalResponse> {
    return this.get(`/ontology/terms/${termId}/ancestors`, params)
  }

  async getDescendants(termId: string, params?: {
    relationship_type?: string
    namespace?: string
    max_depth?: number
  }): Promise<TraversalResponse> {
    return this.get(`/ontology/terms/${termId}/descendants`, params)
  }

  async getParents(termId: string, namespace: string): Promise<Relationship[]> {
    return this.get(`/ontology/terms/${termId}/parents`, { namespace })
  }

  async getChildren(termId: string, namespace: string): Promise<Relationship[]> {
    return this.get(`/ontology/terms/${termId}/children`, { namespace })
  }
}
