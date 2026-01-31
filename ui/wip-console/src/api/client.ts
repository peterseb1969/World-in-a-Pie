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
  BulkCreateTemplateRequest,
  TemplateBulkOperationResponse,
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
  BulkCreateDocumentRequest,
  DocumentBulkOperationResponse,
  TableViewResponse,
  TableViewParams,
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
        const message = error.response?.data?.detail || error.message
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

  async updateTemplate(id: string, data: UpdateTemplateRequest): Promise<Template> {
    const response = await this.client.put<Template>(`/templates/${id}`, data)
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

  async createDocument(data: CreateDocumentRequest): Promise<Document> {
    const response = await this.client.post<Document>('/documents', data)
    return response.data
  }

  async updateDocument(templateId: string, data: Record<string, unknown>): Promise<Document> {
    // Document Store uses upsert - POST with same identity fields creates a new version
    const response = await this.client.post<Document>('/documents', {
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
// EXPORTS
// =============================================================================

export const defStoreClient = new DefStoreClient()
export const templateStoreClient = new TemplateStoreClient()
export const documentStoreClient = new DocumentStoreClient()

// Default export for backward compatibility
export default defStoreClient
