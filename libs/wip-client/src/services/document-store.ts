import { BaseService } from './base.js'
import type { BulkResponse, BulkResultItem } from '../types/common.js'
import type {
  Document,
  DocumentListResponse,
  CreateDocumentRequest,
  DocumentQueryParams,
  DocumentQueryRequest,
  DocumentValidationResponse,
  ValidateDocumentRequest,
  DocumentVersionResponse,
  TableViewResponse,
  TableViewParams,
  ImportPreviewResponse,
  ImportDocumentsResponse,
  ImportDocumentsOptions,
  ReplayRequest,
  ReplaySessionResponse,
} from '../types/document.js'

export class DocumentStoreService extends BaseService {
  constructor(transport: import('../http.js').FetchTransport) {
    super(transport, '/api/document-store')
  }

  // ---- Documents ----

  async listDocuments(params?: DocumentQueryParams): Promise<DocumentListResponse> {
    return this.get('/documents', params)
  }

  async getDocument(id: string, version?: number): Promise<Document> {
    return this.get(`/documents/${id}`, version !== undefined ? { version } : undefined)
  }

  async createDocument(data: CreateDocumentRequest): Promise<BulkResultItem> {
    return this.bulkWriteOne('/documents', data)
  }

  async createDocuments(data: CreateDocumentRequest[]): Promise<BulkResponse> {
    return this.bulkWrite('/documents', data)
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
    }, 'DELETE')
  }

  async archiveDocument(id: string, archivedBy?: string): Promise<BulkResultItem> {
    return this.bulkWriteOne('/documents/archive', { id, archived_by: archivedBy })
  }

  // NOTE: restoreDocument was removed — no /documents/{id}/restore endpoint exists in the API spec.

  // ---- Validation ----

  async validateDocument(data: ValidateDocumentRequest): Promise<DocumentValidationResponse> {
    return this.post('/validation/validate', data)
  }

  // ---- Versions ----

  async getVersions(id: string): Promise<DocumentVersionResponse> {
    return this.get(`/documents/${id}/versions`)
  }

  async getVersion(id: string, version: number): Promise<Document> {
    return this.get(`/documents/${id}/versions/${version}`)
  }

  // ---- Table View ----

  async getTableView(templateId: string, params?: TableViewParams): Promise<TableViewResponse> {
    return this.get(`/table/${templateId}`, params)
  }

  async exportTableCsv(
    templateId: string,
    params?: { status?: string; include_metadata?: boolean; max_cross_product?: number },
  ): Promise<Blob> {
    return this.getBlob(`/table/${templateId}/csv`, params)
  }

  // ---- Additional Lookups ----

  async getLatestDocument(id: string): Promise<Document> {
    return this.get(`/documents/${id}/latest`)
  }

  async getDocumentByIdentity(
    identityHash: string,
    includeInactive?: boolean,
  ): Promise<Document> {
    return this.get(`/documents/by-identity/${identityHash}`, includeInactive !== undefined ? { include_inactive: includeInactive } : undefined)
  }

  async queryDocuments(body: DocumentQueryRequest): Promise<DocumentListResponse> {
    return this.post('/documents/query', body)
  }

  // ---- Import ----

  async previewImport(file: Blob, filename: string): Promise<ImportPreviewResponse> {
    const form = new FormData()
    form.append('file', file, filename)
    return this.postFormData('/import/preview', form)
  }

  async importDocuments(
    file: Blob,
    filename: string,
    options: ImportDocumentsOptions,
  ): Promise<ImportDocumentsResponse> {
    const form = new FormData()
    form.append('file', file, filename)
    form.append('template_id', options.template_id)
    form.append('column_mapping', JSON.stringify(options.column_mapping))
    form.append('namespace', options.namespace)
    if (options.skip_errors) {
      form.append('skip_errors', 'true')
    }
    return this.postFormData('/import', form)
  }

  // ---- Replay ----

  async startReplay(request: ReplayRequest = {}): Promise<ReplaySessionResponse> {
    return this.post('/replay/start', request)
  }

  async getReplayStatus(sessionId: string): Promise<ReplaySessionResponse> {
    return this.get(`/replay/${sessionId}`)
  }

  async pauseReplay(sessionId: string): Promise<ReplaySessionResponse> {
    return this.post(`/replay/${sessionId}/pause`)
  }

  async resumeReplay(sessionId: string): Promise<ReplaySessionResponse> {
    return this.post(`/replay/${sessionId}/resume`)
  }

  async cancelReplay(sessionId: string): Promise<ReplaySessionResponse> {
    return this.del(`/replay/${sessionId}`)
  }
}
