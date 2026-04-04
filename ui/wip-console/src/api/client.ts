import axios, { AxiosInstance, AxiosError } from 'axios'
import type {
  // Terminology types
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
  // Template types
  Template,
  TemplateListResponse,
  CreateTemplateRequest,
  UpdateTemplateRequest,
  ValidateTemplateRequest,
  ValidateTemplateResponse,
  // Document types
  Document,
  DocumentListResponse,
  CreateDocumentRequest,
  DocumentValidationResponse,
  ValidateDocumentRequest,
  DocumentVersionResponse,
  DocumentQueryParams,
  TableViewResponse,
  TableViewParams,
  // File types
  FileEntity,
  FileListResponse,
  FileDownloadResponse,
  UpdateFileMetadataRequest,
  FileIntegrityResponse,
  FileQueryParams,
  // Ontology types
  Relationship,
  RelationshipListResponse,
  CreateRelationshipRequest,
  DeleteRelationshipRequest,
  TraversalResponse,
  // Shared types
  ApiError,
  BulkResultItem,
  BulkResponse,
  // Registry types
  RegistryEntryListResponse,
  RegistryLookupResponse,
  RegistryBrowseParams,
  RegistrySearchResponse,
  RegistrySearchParams,
  RegistryEntryFull,
  AddSynonymRequest,
  RemoveSynonymRequest,
  MergeRequest
} from '@/types'

// =============================================================================
// AUTH TYPES
// =============================================================================

export type AuthConfig = {
  type: 'api_key' | 'bearer'
  value: string
} | null

// Callback for handling auth errors (set by the app to enable redirect)
let onAuthError: (() => void) | null = null

export function setAuthErrorHandler(handler: () => void) {
  onAuthError = handler
}

// =============================================================================
// BASE CLIENT WITH AUTH SUPPORT
// =============================================================================

abstract class BaseApiClient {
  protected client: AxiosInstance
  protected auth: AuthConfig = null

  constructor(baseURL: string) {
    this.client = axios.create({
      baseURL,
      headers: {
        'Content-Type': 'application/json'
      }
    })

    this.client.interceptors.request.use((config) => {
      if (this.auth) {
        if (this.auth.type === 'api_key') {
          config.headers['X-API-Key'] = this.auth.value
        } else if (this.auth.type === 'bearer') {
          config.headers['Authorization'] = `Bearer ${this.auth.value}`
        }
      }
      return config
    })

    this.client.interceptors.response.use(
      (response) => response,
      (error: AxiosError<ApiError>) => {
        const status = error.response?.status
        const detail = error.response?.data?.detail
        let message: string
        if (typeof detail === 'object' && detail !== null) {
          const d = detail as Record<string, unknown>
          message = String(d.message || d.error || JSON.stringify(detail))
        } else {
          message = String(detail || error.message)
        }

        // Handle auth errors (401 Unauthorized, 403 Forbidden)
        if (status === 401 || status === 403) {
          console.error(`[API] Auth error (${status}):`, message)
          // Only trigger auth error handler if we had auth configured
          // (i.e., session expired, not just unauthenticated request)
          if (this.auth !== null) {
            this.auth = null
            if (onAuthError) {
              onAuthError()
            }
            // Return a quiet rejection — the auth error handler already redirects
            // Using a custom property to suppress toast display
            const err = new Error('Session expired. Please log in again.')
            ;(err as any).isAuthError = true
            return Promise.reject(err)
          }
          // If no auth was set, just return the error without triggering logout
          const err = new Error(message || 'Authentication required')
          ;(err as any).isAuthError = true
          return Promise.reject(err)
        }

        return Promise.reject(new Error(message))
      }
    )
  }

  setAuth(auth: AuthConfig) {
    this.auth = auth
  }

  protected async bulkWrite<T>(url: string, items: T[], method: 'post' | 'put' | 'patch' | 'delete' = 'post'): Promise<BulkResponse> {
    let response
    if (method === 'delete') {
      response = await this.client.delete<BulkResponse>(url, { data: items })
    } else {
      response = await this.client[method]<BulkResponse>(url, items)
    }
    return response.data
  }

  protected async bulkWriteOne<T>(url: string, item: T, method: 'post' | 'put' | 'patch' | 'delete' = 'post'): Promise<BulkResultItem> {
    const resp = await this.bulkWrite(url, [item], method)
    const result = resp.results[0]
    if (result.status === 'error') throw new Error(result.error || 'Operation failed')
    return result
  }

  // Legacy method for backward compatibility
  setApiKey(key: string) {
    if (key) {
      this.auth = { type: 'api_key', value: key }
    } else {
      this.auth = null
    }
  }

  getApiKey(): string {
    return this.auth?.type === 'api_key' ? this.auth.value : ''
  }
}

// =============================================================================
// DEF-STORE CLIENT (Terminologies & Terms)
// =============================================================================

class DefStoreClient extends BaseApiClient {
  constructor() {
    super('/api/def-store')
  }

  // ===========================================================================
  // TERMINOLOGY ENDPOINTS
  // ===========================================================================

  async listTerminologies(params?: {
    page?: number
    page_size?: number
    status?: string
    value?: string
    namespace?: string
  }): Promise<TerminologyListResponse> {
    const response = await this.client.get<TerminologyListResponse>('/terminologies', { params })
    return response.data
  }

  async getTerminology(id: string): Promise<Terminology> {
    const response = await this.client.get<Terminology>(`/terminologies/${id}`)
    return response.data
  }

  async createTerminology(data: CreateTerminologyRequest): Promise<BulkResultItem> {
    return this.bulkWriteOne('/terminologies', data)
  }

  async updateTerminology(id: string, data: UpdateTerminologyRequest): Promise<BulkResultItem> {
    return this.bulkWriteOne('/terminologies', { ...data, terminology_id: id }, 'put')
  }

  async deleteTerminology(id: string, options?: {
    force?: boolean
    hardDelete?: boolean
  }): Promise<BulkResultItem> {
    return this.bulkWriteOne('/terminologies', {
      id,
      force: options?.force,
      hard_delete: options?.hardDelete,
    }, 'delete')
  }

  // ===========================================================================
  // TERM ENDPOINTS
  // ===========================================================================

  async listTerms(terminologyId: string, params?: {
    page?: number
    page_size?: number
    status?: string
    search?: string
    namespace?: string
  }): Promise<TermListResponse> {
    const response = await this.client.get<TermListResponse>(
      `/terminologies/${terminologyId}/terms`,
      { params }
    )
    return response.data
  }

  async getTerm(termId: string): Promise<Term> {
    const response = await this.client.get<Term>(`/terms/${termId}`)
    return response.data
  }

  async createTerm(terminologyId: string, data: CreateTermRequest): Promise<BulkResultItem> {
    return this.bulkWriteOne(`/terminologies/${terminologyId}/terms`, data)
  }

  async updateTerm(termId: string, data: UpdateTermRequest): Promise<BulkResultItem> {
    return this.bulkWriteOne('/terms', { ...data, term_id: termId }, 'put')
  }

  async deprecateTerm(termId: string, data: DeprecateTermRequest): Promise<BulkResultItem> {
    return this.bulkWriteOne('/terms/deprecate', { ...data, term_id: termId })
  }

  async deleteTerm(termId: string, options?: { hardDelete?: boolean }): Promise<BulkResultItem> {
    return this.bulkWriteOne('/terms', {
      id: termId,
      hard_delete: options?.hardDelete,
    }, 'delete')
  }

  async bulkCreateTerms(
    terminologyId: string,
    terms: CreateTermRequest[]
  ): Promise<BulkResponse> {
    return this.bulkWrite(`/terminologies/${terminologyId}/terms`, terms)
  }

  // ===========================================================================
  // IMPORT/EXPORT ENDPOINTS
  // ===========================================================================

  async importTerminology(data: ImportTerminologyRequest): Promise<{
    terminology: Terminology
    terms_result: BulkResponse
    relationships_result?: { total: number; created: number; skipped: number; errors: number }
  }> {
    const response = await this.client.post('/import-export/import', data)
    return response.data
  }

  async exportTerminology(
    terminologyId: string,
    format: 'json' | 'csv' = 'json',
    includeInactive: boolean = false,
    includeRelationships: boolean = false
  ): Promise<ExportTerminologyResponse | string> {
    const response = await this.client.get(`/import-export/export/${terminologyId}`, {
      params: { format, include_inactive: includeInactive, include_relationships: includeRelationships }
    })
    return response.data
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
    }
  ): Promise<{
    terminology: { terminology_id: string; value: string; label: string; status: string }
    terms: { total: number; created: number; skipped: number; errors: number }
    relationships: { total: number; created: number; skipped: number; errors: number; predicate_distribution: Record<string, number>; error_samples?: string[] }
    elapsed_seconds: number
  }> {
    const response = await this.client.post('/import-export/import-ontology', data, {
      params: options
    })
    return response.data
  }

  // ===========================================================================
  // VALIDATION ENDPOINTS
  // ===========================================================================

  async validateValue(data: ValidateValueRequest): Promise<ValidateValueResponse> {
    const response = await this.client.post<ValidateValueResponse>('/validate', data)
    return response.data
  }

  async bulkValidate(data: BulkValidateRequest): Promise<BulkValidateResponse> {
    const response = await this.client.post<BulkValidateResponse>('/validate/bulk', data)
    return response.data
  }

  // ===========================================================================
  // ONTOLOGY / RELATIONSHIP ENDPOINTS
  // ===========================================================================

  async listRelationships(params: {
    term_id: string
    direction?: string
    relationship_type?: string
    namespace?: string
    page?: number
    page_size?: number
  }): Promise<RelationshipListResponse> {
    const response = await this.client.get<RelationshipListResponse>(
      '/ontology/relationships',
      { params }
    )
    return response.data
  }

  async listAllRelationships(params?: {
    namespace?: string
    relationship_type?: string
    source_terminology_id?: string
    status?: string
    page?: number
    page_size?: number
  }): Promise<RelationshipListResponse> {
    const response = await this.client.get<RelationshipListResponse>(
      '/ontology/relationships/all',
      { params }
    )
    return response.data
  }

  async createRelationships(items: CreateRelationshipRequest[], namespace?: string): Promise<BulkResponse> {
    const params = namespace ? { namespace } : undefined
    const response = await this.client.post<BulkResponse>(
      '/ontology/relationships',
      items,
      { params }
    )
    return response.data
  }

  async deleteRelationships(items: DeleteRelationshipRequest[], namespace?: string): Promise<BulkResponse> {
    const params = namespace ? { namespace } : undefined
    const response = await this.client.delete<BulkResponse>(
      '/ontology/relationships',
      { data: items, params }
    )
    return response.data
  }

  async getAncestors(termId: string, params?: {
    relationship_type?: string
    namespace?: string
    max_depth?: number
  }): Promise<TraversalResponse> {
    const response = await this.client.get<TraversalResponse>(
      `/ontology/terms/${termId}/ancestors`,
      { params }
    )
    return response.data
  }

  async getTermDescendants(termId: string, params?: {
    relationship_type?: string
    namespace?: string
    max_depth?: number
  }): Promise<TraversalResponse> {
    const response = await this.client.get<TraversalResponse>(
      `/ontology/terms/${termId}/descendants`,
      { params }
    )
    return response.data
  }

  async getParents(termId: string, namespace?: string): Promise<Relationship[]> {
    const params = namespace ? { namespace } : undefined
    const response = await this.client.get<Relationship[]>(
      `/ontology/terms/${termId}/parents`,
      { params }
    )
    return response.data
  }

  async getChildren(termId: string, namespace?: string): Promise<Relationship[]> {
    const params = namespace ? { namespace } : undefined
    const response = await this.client.get<Relationship[]>(
      `/ontology/terms/${termId}/children`,
      { params }
    )
    return response.data
  }
}

// =============================================================================
// TEMPLATE-STORE CLIENT (Templates)
// =============================================================================

class TemplateStoreClient extends BaseApiClient {
  constructor() {
    super('/api/template-store')
  }

  // ===========================================================================
  // TEMPLATE ENDPOINTS
  // ===========================================================================

  async listTemplates(params?: {
    page?: number
    page_size?: number
    status?: string
    extends?: string
    value?: string
    latest_only?: boolean
    namespace?: string
  }): Promise<TemplateListResponse> {
    const response = await this.client.get<TemplateListResponse>('/templates', { params })
    return response.data
  }

  async getTemplate(id: string, version?: number): Promise<Template> {
    const params = version ? { version } : undefined
    const response = await this.client.get<Template>(`/templates/${id}`, { params })
    return response.data
  }

  async getTemplateRaw(id: string, version?: number): Promise<Template> {
    const params = version ? { version } : undefined
    const response = await this.client.get<Template>(`/templates/${id}/raw`, { params })
    return response.data
  }

  async getTemplateByValue(value: string): Promise<Template> {
    const response = await this.client.get<Template>(`/templates/by-value/${value}`)
    return response.data
  }

  async getTemplateByValueRaw(value: string, namespace: string): Promise<Template> {
    const response = await this.client.get<Template>(`/templates/by-value/${value}/raw`, { params: { namespace } })
    return response.data
  }

  async getTemplateVersions(value: string): Promise<TemplateListResponse> {
    const response = await this.client.get<TemplateListResponse>(`/templates/by-value/${value}/versions`)
    return response.data
  }

  async getTemplateByValueAndVersion(value: string, version: number): Promise<Template> {
    const response = await this.client.get<Template>(`/templates/by-value/${value}/versions/${version}`)
    return response.data
  }

  async createTemplate(data: CreateTemplateRequest): Promise<BulkResultItem> {
    return this.bulkWriteOne('/templates', data)
  }

  async updateTemplate(id: string, data: UpdateTemplateRequest): Promise<BulkResultItem> {
    return this.bulkWriteOne('/templates', { ...data, template_id: id }, 'put')
  }

  async deleteTemplate(id: string, options?: {
    updatedBy?: string
    version?: number
    force?: boolean
    hardDelete?: boolean
  }): Promise<BulkResultItem> {
    return this.bulkWriteOne('/templates', {
      id,
      version: options?.version,
      force: options?.force,
      hard_delete: options?.hardDelete,
      updated_by: options?.updatedBy,
    }, 'delete')
  }

  async validateTemplate(
    id: string,
    request: ValidateTemplateRequest = {}
  ): Promise<ValidateTemplateResponse> {
    const response = await this.client.post<ValidateTemplateResponse>(
      `/templates/${id}/validate`,
      request
    )
    return response.data
  }

  // ===========================================================================
  // ACTIVATION ENDPOINTS
  // ===========================================================================

  async activateTemplate(id: string, params?: {
    namespace?: string
    dry_run?: boolean
  }): Promise<BulkResponse> {
    const response = await this.client.post<BulkResponse>(
      `/templates/${id}/activate`,
      null,
      { params }
    )
    return response.data
  }

  // ===========================================================================
  // INHERITANCE ENDPOINTS
  // ===========================================================================

  async getChildren(id: string): Promise<TemplateListResponse> {
    const response = await this.client.get<TemplateListResponse>(`/templates/${id}/children`)
    return response.data
  }

  async getDescendants(id: string): Promise<TemplateListResponse> {
    const response = await this.client.get<TemplateListResponse>(`/templates/${id}/descendants`)
    return response.data
  }

}

// =============================================================================
// DOCUMENT-STORE CLIENT (Documents)
// =============================================================================

class DocumentStoreClient extends BaseApiClient {
  constructor() {
    super('/api/document-store')
  }

  // ===========================================================================
  // DOCUMENT ENDPOINTS
  // ===========================================================================

  async listDocuments(params?: DocumentQueryParams): Promise<DocumentListResponse> {
    const response = await this.client.get<DocumentListResponse>('/documents', { params })
    return response.data
  }

  async getDocument(id: string, version?: number): Promise<Document> {
    const params = version !== undefined ? { version } : undefined
    const response = await this.client.get<Document>(`/documents/${id}`, { params })
    return response.data
  }

  async createDocument(data: CreateDocumentRequest): Promise<BulkResultItem> {
    return this.bulkWriteOne('/documents', data)
  }

  async updateDocument(templateId: string, data: Record<string, unknown>, namespace?: string): Promise<BulkResultItem> {
    // Document Store uses upsert - POST with same identity fields creates a new version
    const payload: CreateDocumentRequest = {
      template_id: templateId,
      data: data
    }
    if (namespace) {
      payload.namespace = namespace
    }
    return this.bulkWriteOne('/documents', payload)
  }

  async deleteDocument(id: string, options?: {
    updatedBy?: string
    hardDelete?: boolean
    version?: number
  }): Promise<BulkResultItem> {
    return this.bulkWriteOne('/documents', {
      id,
      updated_by: options?.updatedBy,
      hard_delete: options?.hardDelete,
      version: options?.version,
    }, 'delete')
  }

  async archiveDocument(id: string, archivedBy?: string): Promise<BulkResultItem> {
    return this.bulkWriteOne('/documents/archive', { id, archived_by: archivedBy })
  }

  async restoreDocument(id: string, updatedBy?: string): Promise<Document> {
    const params = updatedBy ? { updated_by: updatedBy } : undefined
    const response = await this.client.post<Document>(`/documents/${id}/restore`, null, { params })
    return response.data
  }

  async queryDocuments(request: {
    filters?: { field: string; operator: string; value: unknown }[]
    template_id?: string
    status?: string
    namespace?: string
    page?: number
    page_size?: number
    sort_by?: string
    sort_order?: string
  }): Promise<DocumentListResponse> {
    const response = await this.client.post<DocumentListResponse>('/documents/query', request)
    return response.data
  }

  // ===========================================================================
  // VALIDATION ENDPOINTS
  // ===========================================================================

  async validateDocument(data: ValidateDocumentRequest): Promise<DocumentValidationResponse> {
    const response = await this.client.post<DocumentValidationResponse>('/validation/validate', data)
    return response.data
  }

  // ===========================================================================
  // VERSION ENDPOINTS
  // ===========================================================================

  async getVersions(id: string): Promise<DocumentVersionResponse> {
    const response = await this.client.get<DocumentVersionResponse>(`/documents/${id}/versions`)
    return response.data
  }

  async getVersion(id: string, version: number): Promise<Document> {
    const response = await this.client.get<Document>(`/documents/${id}/versions/${version}`)
    return response.data
  }

  // ===========================================================================
  // TABLE VIEW ENDPOINTS
  // ===========================================================================

  async getTableView(templateId: string, params?: TableViewParams): Promise<TableViewResponse> {
    const response = await this.client.get<TableViewResponse>(`/table/${templateId}`, { params })
    return response.data
  }

  async exportTableCsv(
    templateId: string,
    params?: { status?: string; namespace?: string; include_metadata?: boolean; max_cross_product?: number }
  ): Promise<Blob> {
    const response = await this.client.get(`/table/${templateId}/csv`, {
      params,
      responseType: 'blob'
    })
    return response.data
  }

  // ===========================================================================
  // IMPORT ENDPOINTS
  // ===========================================================================

  async importPreview(file: File): Promise<{
    format: string
    headers: string[]
    sample_rows: Record<string, string>[]
    total_rows: number
    error?: string
  }> {
    const formData = new FormData()
    formData.append('file', file)
    const response = await this.client.post('/import/preview', formData, {
      headers: { 'Content-Type': 'multipart/form-data' }
    })
    return response.data
  }

  async importDocuments(
    file: File,
    templateId: string,
    columnMapping: Record<string, string>,
    namespace: string,
    skipErrors: boolean = false
  ): Promise<{
    total_rows: number
    succeeded: number
    failed: number
    skipped: number
    results: Array<{ row: number; document_id: string; version: number; is_new: boolean }>
    errors: Array<{ row: number; error: string; data?: Record<string, string> }>
    error?: string
  }> {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('template_id', templateId)
    formData.append('column_mapping', JSON.stringify(columnMapping))
    formData.append('namespace', namespace)
    formData.append('skip_errors', String(skipErrors))
    const response = await this.client.post('/import', formData, {
      headers: { 'Content-Type': 'multipart/form-data' }
    })
    return response.data
  }
}

// =============================================================================
// FILE-STORE CLIENT (File Storage)
// =============================================================================

class FileStoreClient extends BaseApiClient {
  constructor() {
    super('/api/document-store/files')
  }

  // ===========================================================================
  // FILE ENDPOINTS
  // ===========================================================================

  async uploadFile(
    file: File,
    metadata?: {
      description?: string
      tags?: string[]
      category?: string
      allowed_templates?: string[]
    }
  ): Promise<FileEntity> {
    const formData = new FormData()
    formData.append('file', file)

    if (metadata?.description) {
      formData.append('description', metadata.description)
    }
    if (metadata?.tags && metadata.tags.length > 0) {
      formData.append('tags', metadata.tags.join(','))
    }
    if (metadata?.category) {
      formData.append('category', metadata.category)
    }
    if (metadata?.allowed_templates && metadata.allowed_templates.length > 0) {
      formData.append('allowed_templates', metadata.allowed_templates.join(','))
    }

    const response = await this.client.post<FileEntity>('', formData, {
      headers: {
        'Content-Type': 'multipart/form-data'
      }
    })
    return response.data
  }

  async listFiles(params?: FileQueryParams): Promise<FileListResponse> {
    const response = await this.client.get<FileListResponse>('', { params })
    return response.data
  }

  async getFile(fileId: string): Promise<FileEntity> {
    const response = await this.client.get<FileEntity>(`/${fileId}`)
    return response.data
  }

  async getDownloadUrl(fileId: string, expiresIn?: number): Promise<FileDownloadResponse> {
    const params = expiresIn ? { expires_in: expiresIn } : undefined
    const response = await this.client.get<FileDownloadResponse>(`/${fileId}/download`, { params })
    return response.data
  }

  async downloadFileContent(fileId: string): Promise<Blob> {
    const response = await this.client.get(`/${fileId}/content`, {
      responseType: 'blob'
    })
    return response.data
  }

  async updateMetadata(fileId: string, data: UpdateFileMetadataRequest): Promise<BulkResultItem> {
    return this.bulkWriteOne('', { ...data, file_id: fileId }, 'patch')
  }

  async deleteFile(fileId: string): Promise<BulkResultItem> {
    return this.bulkWriteOne('', { id: fileId }, 'delete')
  }

  async deleteFiles(fileIds: string[]): Promise<BulkResponse> {
    return this.bulkWrite('', fileIds.map(id => ({ id })), 'delete')
  }

  async hardDeleteFile(fileId: string): Promise<void> {
    await this.client.delete(`/${fileId}/hard`)
  }

  // ===========================================================================
  // UTILITY ENDPOINTS
  // ===========================================================================

  async listOrphans(params?: {
    older_than_hours?: number
    limit?: number
  }): Promise<FileEntity[]> {
    const response = await this.client.get<FileEntity[]>('/orphans/list', { params })
    return response.data
  }

  async findByChecksum(checksum: string): Promise<FileEntity[]> {
    const response = await this.client.get<FileEntity[]>(`/by-checksum/${checksum}`)
    return response.data
  }

  async checkIntegrity(): Promise<FileIntegrityResponse> {
    const response = await this.client.get<FileIntegrityResponse>('/health/integrity')
    return response.data
  }

  async getFileDocuments(fileId: string, page: number = 1, pageSize: number = 10): Promise<{
    items: Array<{
      document_id: string
      template_id: string
      template_value: string | null
      field_path: string
      status: string
      created_at: string | null
    }>
    total: number
    page: number
    page_size: number
    pages: number
  }> {
    const response = await this.client.get(`/${fileId}/documents`, {
      params: { page, page_size: pageSize }
    })
    return response.data
  }

  // ===========================================================================
  // STORAGE STATUS
  // ===========================================================================

  async isStorageEnabled(namespace?: string): Promise<boolean> {
    try {
      // Try listing files - if storage is disabled, this will return 503
      await this.client.get('', { params: { page_size: 1, namespace: namespace ?? 'wip' } })
      return true
    } catch (error) {
      if (axios.isAxiosError(error)) {
        const status = error.response?.status
        // 503 = storage disabled, 401/403 = auth error (treat as not enabled for this check)
        if (status === 503 || status === 401 || status === 403) {
          return false
        }
      }
      throw error
    }
  }
}

// =============================================================================
// REPORTING-SYNC CLIENT (Integrity & Metrics)
// =============================================================================

// Integrity check types
export interface IntegrityIssue {
  type: string
  severity: string
  source: string
  entity_id: string
  entity_value: string | null
  field_path: string | null
  reference: string
  message: string
}

export interface IntegritySummary {
  total_templates: number
  total_documents: number
  documents_checked: number
  templates_with_issues: number
  documents_with_issues: number
  orphaned_terminology_refs: number
  orphaned_template_refs: number
  orphaned_term_refs: number
  inactive_refs: number
}

export interface IntegrityCheckResult {
  status: 'healthy' | 'warning' | 'error' | 'partial'
  checked_at: string
  services_checked: string[]
  services_unavailable: string[]
  summary: IntegritySummary
  issues: IntegrityIssue[]
}

// Search and activity types
export interface SearchResult {
  type: 'terminology' | 'term' | 'template' | 'document' | 'file'
  id: string
  value: string | null
  label: string | null
  status: string | null
  description: string | null
  updated_at: string | null
}

export interface SearchResponse {
  query: string
  results: SearchResult[]
  counts: Record<string, number>
  total: number
}

export interface ActivityItem {
  type: 'terminology' | 'term' | 'template' | 'document' | 'file'
  action: 'created' | 'updated' | 'deleted' | 'deprecated'
  entity_id: string
  entity_value: string | null
  entity_label: string | null
  timestamp: string
  user: string | null
  version: number | null
  details: Record<string, unknown> | null
}

export interface ActivityResponse {
  activities: ActivityItem[]
  total: number
}

export interface DocumentReference {
  document_id: string
  template_id: string
  template_value: string | null
  field_path: string
  status: string
  created_at: string | null
}

export interface TermDocumentsResponse {
  term_id: string
  documents: DocumentReference[]
  total: number
}

// Entity references types
export interface EntityReference {
  ref_type: 'template' | 'terminology' | 'term'
  ref_id: string
  ref_value: string | null
  ref_label: string | null
  field_path: string | null
  status: 'valid' | 'broken' | 'inactive'
  error: string | null
}

export interface EntityDetails {
  entity_type: 'document' | 'template' | 'terminology' | 'term' | 'file'
  entity_id: string
  entity_value: string | null
  entity_label: string | null
  entity_status: string | null
  version: number | null
  created_at: string | null
  updated_at: string | null
  data: Record<string, unknown> | null
  references: EntityReference[]
  valid_refs: number
  broken_refs: number
  inactive_refs: number
}

export interface EntityReferencesResponse {
  entity: EntityDetails | null
  error: string | null
}

// Incoming reference (what references this entity)
export interface IncomingReference {
  entity_type: 'document' | 'template'
  entity_id: string
  entity_value: string | null
  entity_label: string | null
  entity_status: string | null
  field_path: string | null
  reference_type: 'uses_template' | 'extends' | 'template_ref' | 'terminology_ref' | 'term_ref' | 'file_ref'
}

export interface ReferencedByResponse {
  entity_type: 'document' | 'template' | 'terminology' | 'term' | 'file'
  entity_id: string
  entity_value: string | null
  entity_label: string | null
  referenced_by: IncomingReference[]
  total: number
  error: string | null
}

class ReportingSyncClient extends BaseApiClient {
  constructor() {
    super('/api/reporting-sync')
  }

  async getIntegrityCheck(params?: {
    template_status?: string
    document_status?: string
    template_limit?: number
    document_limit?: number
    check_term_refs?: boolean
    recent_first?: boolean
  }): Promise<IntegrityCheckResult> {
    const response = await this.client.get<IntegrityCheckResult>('/health/integrity', { params })
    return response.data
  }

  async healthCheck(): Promise<boolean> {
    try {
      const response = await this.client.get('/health')
      return response.status === 200
    } catch {
      return false
    }
  }

  // ===========================================================================
  // SEARCH ENDPOINTS
  // ===========================================================================

  async search(params: {
    query: string
    types?: string[]
    status?: string
    namespace?: string
    limit?: number
  }): Promise<SearchResponse> {
    const response = await this.client.post<SearchResponse>('/search', params)
    return response.data
  }

  async getRecentActivity(params?: {
    types?: string
    namespace?: string
    limit?: number
  }): Promise<ActivityResponse> {
    const response = await this.client.get<ActivityResponse>('/activity/recent', { params })
    return response.data
  }

  async getTermDocuments(termId: string, limit?: number): Promise<TermDocumentsResponse> {
    const response = await this.client.get<TermDocumentsResponse>(
      `/references/term/${termId}/documents`,
      { params: limit ? { limit } : undefined }
    )
    return response.data
  }

  async getEntityReferences(
    entityType: 'document' | 'template' | 'terminology' | 'term' | 'file',
    entityId: string
  ): Promise<EntityReferencesResponse> {
    const response = await this.client.get<EntityReferencesResponse>(
      `/entity/${entityType}/${entityId}/references`
    )
    return response.data
  }

  async getReferencedBy(
    entityType: 'document' | 'template' | 'terminology' | 'term' | 'file',
    entityId: string,
    limit: number = 100
  ): Promise<ReferencedByResponse> {
    const response = await this.client.get<ReferencedByResponse>(
      `/entity/${entityType}/${entityId}/referenced-by`,
      { params: { limit } }
    )
    return response.data
  }
}

// =============================================================================
// REGISTRY CLIENT (Namespaces & ID Pools)
// =============================================================================

/**
 * User-facing namespace for organizing data.
 */
export interface Namespace {
  prefix: string
  description: string
  isolation_mode: 'open' | 'strict'
  allowed_external_refs: string[]
  id_config: Record<string, unknown>
  status: 'active' | 'archived' | 'deleted'
  created_at: string
  created_by: string | null
  updated_at: string
  updated_by: string | null
}

export interface NamespaceStats {
  prefix: string
  description: string
  isolation_mode: string
  status: string
  entity_counts: Record<string, number>
}

export interface CreateNamespaceRequest {
  prefix: string
  description?: string
  isolation_mode?: 'open' | 'strict'
  allowed_external_refs?: string[]
  id_config?: Record<string, IdAlgorithmConfig>
  created_by?: string
}

export interface IdAlgorithmConfig {
  algorithm: 'uuid7' | 'prefixed' | 'nanoid'
  prefix?: string
  pad?: number
  length?: number
}

class RegistryClient extends BaseApiClient {
  constructor() {
    super('/api/registry')
  }

  // ===========================================================================
  // NAMESPACE ENDPOINTS (Primary - new naming)
  // ===========================================================================

  async listNamespaces(includeArchived: boolean = false): Promise<Namespace[]> {
    const response = await this.client.get<Namespace[]>('/namespaces', {
      params: { include_archived: includeArchived }
    })
    return response.data
  }

  async getMyNamespaces(): Promise<{ prefix: string; description: string; permission: string }[]> {
    const response = await this.client.get<{ prefix: string; description: string; permission: string }[]>('/my/namespaces')
    return response.data
  }

  async getNamespace(prefix: string): Promise<Namespace> {
    const response = await this.client.get<Namespace>(`/namespaces/${prefix}`)
    return response.data
  }

  async getNamespaceStats(prefix: string): Promise<NamespaceStats> {
    const response = await this.client.get<NamespaceStats>(`/namespaces/${prefix}/stats`)
    return response.data
  }

  async createNamespace(data: CreateNamespaceRequest): Promise<Namespace> {
    const response = await this.client.post<Namespace>('/namespaces', data)
    return response.data
  }

  async updateNamespace(
    prefix: string,
    data: {
      description?: string
      isolation_mode?: 'open' | 'strict'
      id_config?: Record<string, IdAlgorithmConfig>
      updated_by?: string
    }
  ): Promise<Namespace> {
    const response = await this.client.put<Namespace>(`/namespaces/${prefix}`, data)
    return response.data
  }

  async getIdConfig(prefix: string): Promise<Record<string, unknown>> {
    const ns = await this.getNamespace(prefix)
    return ns.id_config
  }

  async archiveNamespace(prefix: string, archivedBy?: string): Promise<Namespace> {
    const response = await this.client.post<Namespace>(
      `/namespaces/${prefix}/archive`,
      null,
      { params: archivedBy ? { archived_by: archivedBy } : undefined }
    )
    return response.data
  }

  async restoreNamespace(prefix: string, restoredBy?: string): Promise<Namespace> {
    const response = await this.client.post<Namespace>(
      `/namespaces/${prefix}/restore`,
      null,
      { params: restoredBy ? { restored_by: restoredBy } : undefined }
    )
    return response.data
  }

  async deleteNamespace(prefix: string, deletedBy?: string): Promise<void> {
    await this.client.delete(`/namespaces/${prefix}`, {
      params: { confirm: true, deleted_by: deletedBy }
    })
  }

  async initializeWipNamespace(): Promise<Namespace> {
    const response = await this.client.post<Namespace>('/namespaces/initialize-wip')
    return response.data
  }

  // ===========================================================================
  // ENTRY BROWSE & LOOKUP ENDPOINTS
  // ===========================================================================

  async listEntries(params?: RegistryBrowseParams): Promise<RegistryEntryListResponse> {
    const response = await this.client.get<RegistryEntryListResponse>('/entries', { params })
    return response.data
  }

  async lookupEntry(entryId: string): Promise<RegistryLookupResponse> {
    const response = await this.client.post<{ results: RegistryLookupResponse[] }>(
      '/entries/lookup/by-id',
      [{ entry_id: entryId }]
    )
    const result = response.data.results[0]
    return result
  }

  async searchEntries(term: string, options?: {
    namespaces?: string[]
    entityTypes?: string[]
    includeInactive?: boolean
  }): Promise<RegistryLookupResponse[]> {
    const response = await this.client.post<{ results: Array<{ results: Array<Record<string, unknown>> }> }>(
      '/entries/search/by-term',
      [{
        term,
        restrict_to_namespaces: options?.namespaces,
        restrict_to_entity_types: options?.entityTypes,
        include_inactive: options?.includeInactive ?? false,
      }]
    )
    return response.data.results[0]?.results as unknown as RegistryLookupResponse[] ?? []
  }

  // ===========================================================================
  // UNIFIED SEARCH & DETAIL ENDPOINTS
  // ===========================================================================

  async unifiedSearch(params: RegistrySearchParams): Promise<RegistrySearchResponse> {
    const response = await this.client.get<RegistrySearchResponse>('/entries/search', { params })
    return response.data
  }

  async getEntry(entryId: string): Promise<RegistryEntryFull> {
    const response = await this.client.get<RegistryEntryFull>(`/entries/${entryId}`)
    return response.data
  }

  // ===========================================================================
  // MUTATION ENDPOINTS
  // ===========================================================================

  async addSynonym(request: AddSynonymRequest): Promise<{ status: string; registry_id?: string; error?: string }> {
    const response = await this.client.post<{ results: Array<{ status: string; registry_id?: string; error?: string }> }>(
      '/synonyms/add',
      [request]
    )
    return response.data.results[0]
  }

  async removeSynonym(request: RemoveSynonymRequest): Promise<{ status: string; registry_id?: string; error?: string }> {
    const response = await this.client.post<{ results: Array<{ status: string; registry_id?: string; error?: string }> }>(
      '/synonyms/remove',
      [request]
    )
    return response.data.results[0]
  }

  async mergeEntries(request: MergeRequest): Promise<{ status: string; preferred_id?: string; deprecated_id?: string; error?: string }> {
    const response = await this.client.post<{ results: Array<{ status: string; preferred_id?: string; deprecated_id?: string; error?: string }> }>(
      '/synonyms/merge',
      [request]
    )
    return response.data.results[0]
  }

  async deactivateEntry(entryId: string, updatedBy?: string): Promise<{ status: string }> {
    const response = await this.client.delete<{ results: Array<{ status: string }> }>(
      '/entries',
      { data: [{ entry_id: entryId, updated_by: updatedBy }] }
    )
    return response.data.results[0]
  }

  async deactivateEntries(entryIds: string[], updatedBy?: string): Promise<Array<{ status: string; registry_id?: string }>> {
    const response = await this.client.delete<{ results: Array<{ status: string; registry_id?: string }> }>(
      '/entries',
      { data: entryIds.map(id => ({ entry_id: id, updated_by: updatedBy })) }
    )
    return response.data.results
  }
}

// =============================================================================
// EXPORTS
// =============================================================================

export const defStoreClient = new DefStoreClient()
export const templateStoreClient = new TemplateStoreClient()
export const documentStoreClient = new DocumentStoreClient()
export const fileStoreClient = new FileStoreClient()
export const reportingSyncClient = new ReportingSyncClient()
export const registryClient = new RegistryClient()

// Default export for backward compatibility
export default defStoreClient
