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

  async deleteDocument(id: string, updatedBy?: string): Promise<BulkResultItem> {
    return this.bulkWriteOne('/documents', { id, updated_by: updatedBy }, 'DELETE')
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
}
