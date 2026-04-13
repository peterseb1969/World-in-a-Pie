/**
 * Types for the document-store backup/restore endpoints (CASE-23 Phase 3 STEP 7).
 *
 * These mirror the wire contract defined by
 * `components/document-store/src/document_store/models/backup_job.py`.
 *
 * Guardrail 2: `BackupProgressMessage` is the SSE wire envelope. It is
 * deliberately decoupled from the internal `wip_toolkit.models.ProgressEvent`
 * so a future implementation can replace the toolkit without breaking clients.
 */

export type BackupJobKind = 'backup' | 'restore'

export type BackupJobStatus = 'pending' | 'running' | 'complete' | 'failed'

export type RestoreMode = 'restore' | 'fresh'

/**
 * Persistent snapshot of a backup or restore job. Returned by every backup
 * REST endpoint that hands back a job (start, get, list).
 */
export interface BackupJobSnapshot {
  job_id: string
  kind: BackupJobKind
  namespace: string
  status: BackupJobStatus
  phase: string | null
  percent: number | null
  message: string | null
  error: string | null
  created_at: string
  started_at: string | null
  completed_at: string | null
  archive_size: number | null
  options: Record<string, unknown>
  created_by: string
}

/**
 * Request body for `POST /backup/namespaces/{namespace}/backup`.
 *
 * All fields map to keyword arguments of the underlying toolkit
 * `run_export` call. Defaults match the server side, so callers may pass an
 * empty object to take everything.
 *
 * **v1.0 caveat — `include_files`:** see CASE-28. Setting this to `true`
 * against any namespace with non-trivial file content currently causes the
 * archive writer to buffer all blobs in memory. Stick with the default
 * (`false`) until CASE-28 lands.
 */
export interface BackupRequest {
  include_files?: boolean
  include_inactive?: boolean
  skip_documents?: boolean
  skip_closure?: boolean
  skip_synonyms?: boolean
  latest_only?: boolean
  template_prefixes?: string[]
  dry_run?: boolean
}

/**
 * Form fields accompanying a multipart restore upload.
 *
 * **Mode gotcha:** `mode: 'restore'` ignores `target_namespace` and writes
 * back into the archive's source namespace. Use `mode: 'fresh'` (the default
 * here) when restoring into a *new* namespace — that path generates new IDs
 * and honours `target_namespace`.
 */
export interface RestoreOptions {
  mode?: RestoreMode
  target_namespace?: string
  register_synonyms?: boolean
  skip_documents?: boolean
  skip_files?: boolean
  batch_size?: number
  continue_on_error?: boolean
  dry_run?: boolean
}

/**
 * Filter parameters for `GET /backup/jobs`.
 */
export interface ListBackupJobsParams {
  namespace?: string
  status?: BackupJobStatus
  limit?: number
}

/**
 * SSE wire envelope yielded by `streamBackupJobEvents`.
 *
 * Mirrors `BackupProgressMessage` on the server. `phase` is intentionally a
 * free-form string — it is a runtime convention shared between producer and
 * consumer, not a schema contract. Phase names may change between toolkit
 * versions; treat them as opaque strings for display/log purposes.
 */
export interface BackupProgressMessage {
  job_id: string
  status: BackupJobStatus
  phase: string | null
  percent: number | null
  message: string | null
  current: number | null
  total: number | null
  details: Record<string, unknown> | null
}
