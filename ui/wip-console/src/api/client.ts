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
  BulkCreateTermRequest,
  BulkOperationResponse,
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
  TemplateUpdateResponse,
  BulkCreateTemplateRequest,
  TemplateBulkOperationResponse,
  ValidateTemplateRequest,
  ValidateTemplateResponse,
  // Document types
  Document,
  DocumentListResponse,
  CreateDocumentRequest,
  DocumentCreateResponse,
  DocumentValidationResponse,
  ValidateDocumentRequest,
  DocumentVersionResponse,
  DocumentQueryParams,
  BulkCreateDocumentRequest,
  DocumentBulkOperationResponse,
  TableViewResponse,
  TableViewParams,
  // File types
  FileEntity,
  FileListResponse,
  FileDownloadResponse,
  UpdateFileMetadataRequest,
  FileBulkDeleteRequest,
  FileBulkDeleteResponse,
  FileIntegrityResponse,
  FileQueryParams,
  // Shared types
  ApiError
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
        const message = error.response?.data?.detail || error.message

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
    search?: string
    pool_id?: string
  }): Promise<TerminologyListResponse> {
    const response = await this.client.get<TerminologyListResponse>('/terminologies', { params })
    return response.data
  }

  async getTerminology(id: string): Promise<Terminology> {
    const response = await this.client.get<Terminology>(`/terminologies/${id}`)
    return response.data
  }

  async createTerminology(data: CreateTerminologyRequest): Promise<Terminology> {
    const response = await this.client.post<Terminology>('/terminologies', data)
    return response.data
  }

  async updateTerminology(id: string, data: UpdateTerminologyRequest): Promise<Terminology> {
    const response = await this.client.put<Terminology>(`/terminologies/${id}`, data)
    return response.data
  }

  async deleteTerminology(id: string): Promise<void> {
    await this.client.delete(`/terminologies/${id}`)
  }

  // ===========================================================================
  // TERM ENDPOINTS
  // ===========================================================================

  async listTerms(terminologyId: string, params?: {
    page?: number
    page_size?: number
    status?: string
    search?: string
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

  async createTerm(terminologyId: string, data: CreateTermRequest): Promise<Term> {
    const response = await this.client.post<Term>(
      `/terminologies/${terminologyId}/terms`,
      data
    )
    return response.data
  }

  async updateTerm(termId: string, data: UpdateTermRequest): Promise<Term> {
    const response = await this.client.put<Term>(`/terms/${termId}`, data)
    return response.data
  }

  async deprecateTerm(termId: string, data: DeprecateTermRequest): Promise<Term> {
    const response = await this.client.post<Term>(`/terms/${termId}/deprecate`, data)
    return response.data
  }

  async deleteTerm(termId: string): Promise<void> {
    await this.client.delete(`/terms/${termId}`)
  }

  async bulkCreateTerms(
    terminologyId: string,
    data: BulkCreateTermRequest
  ): Promise<BulkOperationResponse> {
    const response = await this.client.post<BulkOperationResponse>(
      `/terminologies/${terminologyId}/terms/bulk`,
      data
    )
    return response.data
  }

  // ===========================================================================
  // IMPORT/EXPORT ENDPOINTS
  // ===========================================================================

  async importTerminology(data: ImportTerminologyRequest): Promise<{
    terminology: Terminology
    terms_result: BulkOperationResponse
  }> {
    const response = await this.client.post('/import-export/import', data)
    return response.data
  }

  async exportTerminology(
    terminologyId: string,
    format: 'json' | 'csv' = 'json',
    includeInactive: boolean = false
  ): Promise<ExportTerminologyResponse | string> {
    const response = await this.client.get(`/import-export/export/${terminologyId}`, {
      params: { format, include_inactive: includeInactive }
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
    code?: string
    latest_only?: boolean
    pool_id?: string
  }): Promise<TemplateListResponse> {
    const response = await this.client.get<TemplateListResponse>('/templates', { params })
    return response.data
  }

  async getTemplate(id: string): Promise<Template> {
    const response = await this.client.get<Template>(`/templates/${id}`)
    return response.data
  }

  async getTemplateRaw(id: string): Promise<Template> {
    const response = await this.client.get<Template>(`/templates/${id}/raw`)
    return response.data
  }

  async getTemplateByCode(code: string): Promise<Template> {
    const response = await this.client.get<Template>(`/templates/by-code/${code}`)
    return response.data
  }

  async getTemplateByCodeRaw(code: string): Promise<Template> {
    const response = await this.client.get<Template>(`/templates/by-code/${code}/raw`)
    return response.data
  }

  async getTemplateVersions(code: string): Promise<TemplateListResponse> {
    const response = await this.client.get<TemplateListResponse>(`/templates/by-code/${code}/versions`)
    return response.data
  }

  async getTemplateByCodeAndVersion(code: string, version: number): Promise<Template> {
    const response = await this.client.get<Template>(`/templates/by-code/${code}/versions/${version}`)
    return response.data
  }

  async createTemplate(data: CreateTemplateRequest): Promise<Template> {
    const response = await this.client.post<Template>('/templates', data)
    return response.data
  }

  async updateTemplate(id: string, data: UpdateTemplateRequest): Promise<TemplateUpdateResponse> {
    const response = await this.client.put<TemplateUpdateResponse>(`/templates/${id}`, data)
    return response.data
  }

  async deleteTemplate(id: string, updatedBy?: string): Promise<void> {
    const params = updatedBy ? { updated_by: updatedBy } : undefined
    await this.client.delete(`/templates/${id}`, { params })
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

  // ===========================================================================
  // BULK ENDPOINTS
  // ===========================================================================

  async createTemplatesBulk(data: BulkCreateTemplateRequest): Promise<TemplateBulkOperationResponse> {
    const response = await this.client.post<TemplateBulkOperationResponse>('/templates/bulk', data)
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

  async getDocument(id: string): Promise<Document> {
    const response = await this.client.get<Document>(`/documents/${id}`)
    return response.data
  }

  async createDocument(data: CreateDocumentRequest): Promise<DocumentCreateResponse> {
    const response = await this.client.post<DocumentCreateResponse>('/documents', data)
    return response.data
  }

  async updateDocument(templateId: string, data: Record<string, unknown>): Promise<DocumentCreateResponse> {
    // Document Store uses upsert - POST with same identity fields creates a new version
    const response = await this.client.post<DocumentCreateResponse>('/documents', {
      template_id: templateId,
      data: data
    })
    return response.data
  }

  async deleteDocument(id: string, updatedBy?: string): Promise<void> {
    const params = updatedBy ? { updated_by: updatedBy } : undefined
    await this.client.delete(`/documents/${id}`, { params })
  }

  async archiveDocument(id: string, updatedBy?: string): Promise<Document> {
    const params = updatedBy ? { updated_by: updatedBy } : undefined
    const response = await this.client.post<Document>(`/documents/${id}/archive`, null, { params })
    return response.data
  }

  async restoreDocument(id: string, updatedBy?: string): Promise<Document> {
    const params = updatedBy ? { updated_by: updatedBy } : undefined
    const response = await this.client.post<Document>(`/documents/${id}/restore`, null, { params })
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
  // BULK ENDPOINTS
  // ===========================================================================

  async createDocumentsBulk(data: BulkCreateDocumentRequest): Promise<DocumentBulkOperationResponse> {
    const response = await this.client.post<DocumentBulkOperationResponse>('/documents/bulk', data)
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
    params?: { status?: string; include_metadata?: boolean }
  ): Promise<Blob> {
    const response = await this.client.get(`/table/${templateId}/csv`, {
      params,
      responseType: 'blob'
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

  async updateMetadata(fileId: string, data: UpdateFileMetadataRequest): Promise<FileEntity> {
    const response = await this.client.patch<FileEntity>(`/${fileId}`, data)
    return response.data
  }

  async deleteFile(fileId: string, force?: boolean): Promise<void> {
    const params = force ? { force: true } : undefined
    await this.client.delete(`/${fileId}`, { params })
  }

  async hardDeleteFile(fileId: string): Promise<void> {
    await this.client.delete(`/${fileId}/hard`)
  }

  // ===========================================================================
  // BULK ENDPOINTS
  // ===========================================================================

  async bulkDelete(request: FileBulkDeleteRequest): Promise<FileBulkDeleteResponse> {
    const response = await this.client.post<FileBulkDeleteResponse>('/bulk/delete', request)
    return response.data
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

  // ===========================================================================
  // STORAGE STATUS
  // ===========================================================================

  async isStorageEnabled(): Promise<boolean> {
    try {
      // Try listing files - if storage is disabled, this will return 503
      await this.client.get('', { params: { page_size: 1 } })
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
  entity_code: string | null
  field_path: string | null
  reference: string
  message: string
}

export interface IntegritySummary {
  total_templates: number
  total_documents: number
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
  type: 'terminology' | 'term' | 'template' | 'document'
  id: string
  code: string | null
  name: string | null
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
  type: 'terminology' | 'term' | 'template' | 'document'
  action: 'created' | 'updated' | 'deleted' | 'deprecated'
  entity_id: string
  entity_code: string | null
  entity_name: string | null
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
  template_code: string | null
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
  ref_code: string | null
  ref_name: string | null
  field_path: string | null
  status: 'valid' | 'broken' | 'inactive'
  error: string | null
}

export interface EntityDetails {
  entity_type: 'document' | 'template' | 'terminology' | 'term'
  entity_id: string
  entity_code: string | null
  entity_name: string | null
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
  entity_code: string | null
  entity_name: string | null
  entity_status: string | null
  field_path: string | null
  reference_type: 'uses_template' | 'extends' | 'template_ref' | 'terminology_ref' | 'term_ref'
}

export interface ReferencedByResponse {
  entity_type: 'document' | 'template' | 'terminology' | 'term'
  entity_id: string
  entity_code: string | null
  entity_name: string | null
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
    limit?: number
  }): Promise<SearchResponse> {
    const response = await this.client.post<SearchResponse>('/search', params)
    return response.data
  }

  async getRecentActivity(params?: {
    types?: string
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
    entityType: 'document' | 'template' | 'terminology' | 'term',
    entityId: string
  ): Promise<EntityReferencesResponse> {
    const response = await this.client.get<EntityReferencesResponse>(
      `/entity/${entityType}/${entityId}/references`
    )
    return response.data
  }

  async getReferencedBy(
    entityType: 'document' | 'template' | 'terminology' | 'term',
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
  status: 'active' | 'archived' | 'deleted'
  created_at: string
  created_by: string | null
  updated_at: string
  updated_by: string | null
  terminologies_pool: string
  terms_pool: string
  templates_pool: string
  documents_pool: string
  files_pool: string
}

export interface NamespaceStats {
  prefix: string
  description: string
  isolation_mode: string
  status: string
  pools: Record<string, number>
}

export interface CreateNamespaceRequest {
  prefix: string
  description?: string
  isolation_mode?: 'open' | 'strict'
  allowed_external_refs?: string[]
  created_by?: string
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
    data: { description?: string; isolation_mode?: 'open' | 'strict'; updated_by?: string }
  ): Promise<Namespace> {
    const response = await this.client.put<Namespace>(`/namespaces/${prefix}`, data)
    return response.data
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
