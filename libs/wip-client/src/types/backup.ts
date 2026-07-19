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

/**
 * Restore mode. `'restore'` is the only mode the server implements today:
 * ID-preserving, each namespace in the archive restores to itself.
 * `'fresh'` is RESERVED — the backend rejects it with 400; the planned
 * new-namespace (remap) mode with re-minted IDs does not exist yet. Kept
 * in the union for forward-compat, but do not send it.
 */
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
 * Only the options the direct backup engine consumes are typed — the
 * endpoint rejects the retired toolkit-era fields (`skip_closure`,
 * `skip_synonyms`, `latest_only`, `template_prefixes`, `dry_run`) with a
 * 400 when set. Blob bytes stream to the server's backup scratch dir, so
 * `include_files: true` is safe at any content volume.
 */
export interface BackupRequest {
  include_files?: boolean
  include_inactive?: boolean
  skip_documents?: boolean
  namespaces?: string[]
  all_namespaces?: boolean
}

/**
 * Form fields accompanying a multipart restore upload.
 *
 * Restore is ID-preserving and writes each namespace in the archive back
 * to ITSELF; every target must be empty. A `target_namespace` differing
 * from the archive's namespace is rejected — re-namespacing needs the
 * planned remap mode (`'fresh'` still 400s server-side). `dry_run` is
 * real: every precondition runs (archive format, empty targets, reporting
 * schema) and the would-restore counts are reported, with nothing
 * written. The retired toolkit-era params (`register_synonyms`,
 * `continue_on_error`) are gone from this type — the endpoint 400s when
 * they are set.
 */
export interface RestoreOptions {
  mode?: RestoreMode
  skip_documents?: boolean
  skip_files?: boolean
  batch_size?: number
  dry_run?: boolean
  /**
   * Drop a stale reporting schema for the target namespace before
   * restoring instead of refusing. A dry run reports the would-drop only.
   */
  drop_stale_reporting?: boolean
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
