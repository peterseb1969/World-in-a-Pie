import { BaseService } from './base.js'
import type { BulkResponse, BulkResultItem } from '../types/common.js'
import type {
  FileEntity,
  FileListResponse,
  FileDownloadResponse,
  UpdateFileMetadataRequest,
  FileIntegrityResponse,
  FileQueryParams,
  FileUploadMetadata,
} from '../types/file.js'

export class FileStoreService extends BaseService {
  constructor(transport: import('../http.js').FetchTransport) {
    super(transport, '/api/document-store/files')
  }

  // ---- Files ----

  async uploadFile(
    file: File | Blob,
    filename?: string,
    metadata?: FileUploadMetadata,
  ): Promise<FileEntity> {
    const formData = new FormData()
    if (file instanceof File) {
      formData.append('file', file)
    } else {
      formData.append('file', file, filename ?? 'upload')
    }

    if (metadata?.description) formData.append('description', metadata.description)
    if (metadata?.tags?.length) formData.append('tags', metadata.tags.join(','))
    if (metadata?.category) formData.append('category', metadata.category)
    if (metadata?.allowed_templates?.length) {
      formData.append('allowed_templates', metadata.allowed_templates.join(','))
    }

    return this.postFormData('', formData)
  }

  async listFiles(params?: FileQueryParams): Promise<FileListResponse> {
    return this.get('', params)
  }

  async getFile(fileId: string): Promise<FileEntity> {
    return this.get(`/${fileId}`)
  }

  async getDownloadUrl(fileId: string, expiresIn?: number): Promise<FileDownloadResponse> {
    return this.get(`/${fileId}/download`, expiresIn ? { expires_in: expiresIn } : undefined)
  }

  async downloadFileContent(fileId: string): Promise<Blob> {
    return this.getBlob(`/${fileId}/content`)
  }

  async updateMetadata(fileId: string, data: UpdateFileMetadataRequest): Promise<BulkResultItem> {
    return this.bulkWriteOne('', { ...data, file_id: fileId }, 'PATCH')
  }

  async deleteFile(fileId: string): Promise<BulkResultItem> {
    return this.bulkWriteOne('', { id: fileId }, 'DELETE')
  }

  async deleteFiles(fileIds: string[]): Promise<BulkResponse> {
    return this.bulkWrite('', fileIds.map(id => ({ id })), 'DELETE')
  }

  async hardDeleteFile(fileId: string): Promise<void> {
    return this.del(`/${fileId}/hard`)
  }

  // ---- Utility ----

  async listOrphans(params?: {
    older_than_hours?: number
    limit?: number
  }): Promise<FileEntity[]> {
    return this.get('/orphans/list', params)
  }

  async findByChecksum(checksum: string): Promise<FileEntity[]> {
    return this.get(`/by-checksum/${checksum}`)
  }

  async checkIntegrity(): Promise<FileIntegrityResponse> {
    return this.get('/health/integrity')
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
    return this.get(`/${fileId}/documents`, { page, page_size: pageSize })
  }
}
