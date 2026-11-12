import { BaseService } from './base.js'
import { WipValidationError } from '../errors.js'
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

/**
 * The `namespace` query parameter, when given. Every route below takes it for
 * resolving a file synonym; a key that can read several namespaces must send
 * it, or a synonym stays unresolved and the call reports `not_found`.
 */
function ns(namespace?: string): Record<string, unknown> | undefined {
  return namespace ? { namespace } : undefined
}

export class FileStoreService extends BaseService {
  constructor(transport: import('../http.js').FetchTransport) {
    super(transport, '/api/document-store/files')
  }

  // ---- Files ----

  /**
   * Upload a file into `namespace`, which the service requires. `filename`
   * and `metadata` may be `undefined`: `uploadFile(blob, 'a.png', undefined, 'my-ns')`.
   */
  async uploadFile(
    file: File | Blob,
    filename: string | undefined,
    metadata: FileUploadMetadata | undefined,
    namespace: string,
  ): Promise<FileEntity> {
    if (!namespace) {
      throw new WipValidationError('uploadFile needs the namespace to store the file in')
    }
    const formData = new FormData()
    if (file instanceof File) {
      formData.append('file', file)
    } else {
      formData.append('file', file, filename ?? 'upload')
    }

    formData.append('namespace', namespace)
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

  async getFile(fileId: string, namespace?: string): Promise<FileEntity> {
    return this.get(`/${fileId}`, ns(namespace))
  }

  async getDownloadUrl(fileId: string, expiresIn?: number, namespace?: string): Promise<FileDownloadResponse> {
    return this.get(`/${fileId}/download`, {
      ...(expiresIn ? { expires_in: expiresIn } : {}),
      ...ns(namespace),
    })
  }

  async downloadFileContent(fileId: string, namespace?: string): Promise<Blob> {
    return this.getBlob(`/${fileId}/content`, ns(namespace))
  }

  async updateMetadata(
    fileId: string,
    data: UpdateFileMetadataRequest,
    namespace?: string,
  ): Promise<BulkResultItem> {
    return this.bulkWriteOne('', { ...data, file_id: fileId }, 'PATCH', ns(namespace))
  }

  async deleteFile(fileId: string, namespace?: string): Promise<BulkResultItem> {
    return this.bulkWriteOne('', { id: fileId }, 'DELETE', ns(namespace))
  }

  async deleteFiles(fileIds: string[], namespace?: string): Promise<BulkResponse> {
    return this.bulkWrite('', fileIds.map(id => ({ id })), 'DELETE', ns(namespace))
  }

  /**
   * Bring a soft-deleted file back under the same id. Result status is
   * `reactivated`, or `unchanged` for a file that was not deleted; refused
   * with `invalid_state` (details.rule `storage_missing`) when the bytes are
   * gone from storage — upload them again instead.
   */
  async reactivateFile(fileId: string, namespace?: string): Promise<BulkResultItem> {
    return this.bulkWriteOne('/reactivate', { id: fileId }, 'POST', ns(namespace))
  }

  async reactivateFiles(fileIds: string[], namespace?: string): Promise<BulkResponse> {
    return this.bulkWrite('/reactivate', fileIds.map(id => ({ id })), 'POST', ns(namespace))
  }

  async hardDeleteFile(fileId: string, namespace?: string): Promise<void> {
    return this.del(`/${fileId}/hard`, undefined, ns(namespace))
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

  async getFileDocuments(
    fileId: string,
    page: number = 1,
    pageSize: number = 10,
    namespace?: string,
  ): Promise<{
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
    return this.get(`/${fileId}/documents`, { page, page_size: pageSize, ...ns(namespace) })
  }
}
