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
  PatchDocumentRequest,
  TableViewResponse,
  TableViewParams,
  ImportPreviewResponse,
  ImportDocumentsResponse,
  ImportDocumentsOptions,
  ReplayRequest,
  ReplaySessionResponse,
} from '../types/document.js'
import type {
  BackupJobSnapshot,
  BackupProgressMessage,
  BackupRequest,
  ListBackupJobsParams,
  RestoreOptions,
} from '../types/backup.js'

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

  /**
   * Apply an RFC 7396 JSON Merge Patch to a document.
   *
   * Single-item convenience that wraps the bulk PATCH endpoint and unwraps the
   * single result. Throws {@link WipBulkItemError} (with `errorCode` populated)
   * on a per-item failure (e.g. `not_found`, `validation_failed`,
   * `concurrency_conflict`, `identity_field_change`).
   *
   * Identity fields cannot be changed via PATCH — use {@link createDocument}
   * to create a new document instead.
   */
  async updateDocument(
    documentId: string,
    patch: Record<string, unknown>,
    options?: { ifMatch?: number },
  ): Promise<BulkResultItem> {
    const item: PatchDocumentRequest = { document_id: documentId, patch }
    if (options?.ifMatch !== undefined) {
      item.if_match = options.ifMatch
    }
    return this.bulkWriteOne('/documents', item, 'PATCH')
  }

  /**
   * Bulk PATCH /documents — apply RFC 7396 merge patches to multiple documents
   * in a single round-trip. Each item is processed independently; per-item
   * failures appear in the response with a populated `error_code`.
   */
  async updateDocuments(items: PatchDocumentRequest[]): Promise<BulkResponse> {
    return this.bulkWrite('/documents', items, 'PATCH')
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

  // ---- Backup / Restore (CASE-23 Phase 3 STEP 7) ----
  //
  // Wraps the document-store /backup REST surface. The server runs the
  // export/import in a worker thread; these methods return a job snapshot
  // immediately (HTTP 202 on start). Use `getBackupJob` for polling or
  // `streamBackupJobEvents` for an async iterator over SSE progress events.

  /**
   * Start a namespace backup. Returns the initial job snapshot (status
   * `pending` or `running`).
   *
   * Pass an empty object to take all defaults. Note `include_files`
   * defaults to `false` — see CASE-28 before setting it to `true`.
   */
  async startBackup(
    namespace: string,
    request: BackupRequest = {},
  ): Promise<BackupJobSnapshot> {
    return this.post(`/backup/namespaces/${namespace}/backup`, request)
  }

  /**
   * Restore a namespace from an uploaded archive. The archive is streamed
   * to disk on the server, so multi-GB uploads do not buffer in memory.
   *
   * **Mode gotcha:** `mode: 'restore'` writes back into the archive's source
   * namespace and ignores `target_namespace`. Use `mode: 'fresh'` when
   * restoring into a different namespace.
   */
  async startRestore(
    namespace: string,
    archive: Blob | File,
    options: RestoreOptions = {},
    filename = 'archive.zip',
  ): Promise<BackupJobSnapshot> {
    const form = new FormData()
    form.append('archive', archive, filename)
    if (options.mode !== undefined) form.append('mode', options.mode)
    if (options.target_namespace !== undefined)
      form.append('target_namespace', options.target_namespace)
    if (options.register_synonyms !== undefined)
      form.append('register_synonyms', String(options.register_synonyms))
    if (options.skip_documents !== undefined)
      form.append('skip_documents', String(options.skip_documents))
    if (options.skip_files !== undefined)
      form.append('skip_files', String(options.skip_files))
    if (options.batch_size !== undefined)
      form.append('batch_size', String(options.batch_size))
    if (options.continue_on_error !== undefined)
      form.append('continue_on_error', String(options.continue_on_error))
    if (options.dry_run !== undefined)
      form.append('dry_run', String(options.dry_run))
    return this.postFormData(`/backup/namespaces/${namespace}/restore`, form)
  }

  /** Get the latest persisted snapshot for a backup or restore job. */
  async getBackupJob(jobId: string): Promise<BackupJobSnapshot> {
    return this.get(`/backup/jobs/${jobId}`)
  }

  /** List recent backup/restore jobs, optionally filtered. */
  async listBackupJobs(params?: ListBackupJobsParams): Promise<BackupJobSnapshot[]> {
    return this.get('/backup/jobs', params)
  }

  /**
   * Download the archive produced by a completed backup job.
   *
   * Throws `WipConflictError` (409) if the job is not yet complete,
   * `WipNotFoundError` (404) if the job_id is unknown, or `WipError` (410)
   * if the archive file has already been cleaned up from disk.
   */
  async downloadBackupArchive(jobId: string): Promise<Blob> {
    return this.transport.request<Blob>('GET', `${this.basePath}/backup/jobs/${jobId}/download`, {
      responseType: 'blob',
    })
  }

  /** Delete a backup/restore job and its archive file. */
  async deleteBackupJob(jobId: string): Promise<void> {
    await this.del(`/backup/jobs/${jobId}`)
  }

  /**
   * Async iterator over Server-Sent Events for a backup/restore job.
   *
   * Yields a `BackupProgressMessage` for every change in the job's status,
   * phase, percent or message. Terminates when the job reaches a terminal
   * state (`complete` or `failed`) or when the consumer aborts via
   * `signal`.
   *
   * Example:
   * ```ts
   * const ctrl = new AbortController()
   * for await (const evt of client.documents.streamBackupJobEvents(jobId, ctrl.signal)) {
   *   console.log(evt.phase, evt.percent, evt.message)
   *   if (evt.status === 'complete' || evt.status === 'failed') break
   * }
   * ```
   */
  async *streamBackupJobEvents(
    jobId: string,
    signal?: AbortSignal,
  ): AsyncIterableIterator<BackupProgressMessage> {
    const response = await this.stream('GET', `/backup/jobs/${jobId}/events`, {
      headers: { Accept: 'text/event-stream' },
      signal,
    })
    if (!response.body) {
      throw new Error('SSE response has no body')
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    try {
      while (true) {
        const { value, done } = await reader.read()
        if (done) return
        buffer += decoder.decode(value, { stream: true })

        // SSE messages are separated by a blank line. Each message is a
        // sequence of `field: value` lines.
        let sepIdx: number
        while ((sepIdx = buffer.indexOf('\n\n')) !== -1) {
          const raw = buffer.slice(0, sepIdx)
          buffer = buffer.slice(sepIdx + 2)
          const parsed = parseSseMessage(raw)
          if (parsed && parsed.event === 'progress' && parsed.data) {
            try {
              yield JSON.parse(parsed.data) as BackupProgressMessage
            } catch {
              // Skip malformed payloads — keep the iterator alive.
            }
          }
        }
      }
    } finally {
      try {
        reader.releaseLock()
      } catch {
        /* ignore */
      }
    }
  }
}

interface ParsedSseMessage {
  event: string
  data: string
}

/**
 * Parse a single SSE message block into an `{event, data}` pair.
 *
 * Lines beginning with `:` are comments (used for keep-alive). The `data`
 * field may span multiple lines and is joined with `\n` per the SSE spec.
 * Returns null for comment-only or empty blocks.
 */
function parseSseMessage(raw: string): ParsedSseMessage | null {
  let event = 'message'
  const dataLines: string[] = []
  for (const line of raw.split('\n')) {
    if (!line || line.startsWith(':')) continue
    const colonIdx = line.indexOf(':')
    if (colonIdx === -1) continue
    const field = line.slice(0, colonIdx)
    // SSE allows a single optional space after the colon.
    const value = line.slice(colonIdx + 1).replace(/^ /, '')
    if (field === 'event') event = value
    else if (field === 'data') dataLines.push(value)
  }
  if (dataLines.length === 0) return null
  return { event, data: dataLines.join('\n') }
}
