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

export type BackupJobKind = 'backup' | 'restore' | 'validate'

export type BackupJobStatus = 'pending' | 'running' | 'complete' | 'failed'

/**
 * Restore mode.
 *
 * `'restore'` requires every target namespace to be EMPTY and inserts the
 * archive wholesale, preserving every canonical id.
 *
 * `'merge'` takes the archive as a delta against a namespace that already
 * holds data, also preserving ids. It may target a differently-named
 * namespace, provided the archive's ids are not already registered here.
 *
 * `'fresh'` keeps nothing: every entity is registered anew with a
 * Registry-minted id and every reference between them is rewritten, which is
 * what lets a namespace be restored BESIDE the one it came from — two live
 * copies cannot share a canonical id. Requires `target_namespace`, and that
 * namespace must be empty.
 */
export type RestoreMode = 'restore' | 'merge' | 'fresh'

/**
 * What a merge does when the target already holds a document's identity.
 * `'skip'` (default) keeps the target's version. `'overwrite'` appends the
 * archive's LATEST version on top of the target's head, adopting the
 * target's document_id — both histories survive, and the archive's is not
 * spliced in. On a `versioned: false` template it replaces the single
 * version in place instead, matching that template's own lifecycle.
 *
 * `'newer'` does what `'overwrite'` does, but only where the archive's copy
 * has a more recent `updated_at`. A tie keeps the target — equal timestamps
 * say nothing about which side to prefer — as does a missing or unparseable
 * timestamp on either side, which is reported as a job warning. Across two
 * installs this is only as reliable as the two machines' clocks: UTC removes
 * timezone error, not skew.
 */
export type ClashPolicy = 'skip' | 'overwrite' | 'newer'



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
  warnings?: string[]
  /** Present on `validate` jobs once they complete. */
  result?: NamespaceIntegrityResult | null
  /**
   * Validation jobs a completed restore started, one per namespace it wrote.
   * The restore does not wait for them — its data is committed either way.
   */
  validation_job_ids?: string[]
  created_by: string
}

/**
 * A single referential or identity problem found in a namespace.
 *
 * Distinct from the reporting layer's `IntegrityIssue`, which describes a
 * PostgreSQL-side finding keyed on `entity_id`. This one is a MongoDB-side
 * finding about a specific document version.
 */
export interface NamespaceIntegrityIssue {
  type: string
  severity: 'error' | 'warning' | 'info'
  document_id: string
  template_id: string
  version: number
  field_path: string | null
  reference: string
  message: string
}

export interface NamespaceIntegritySummary {
  total_documents: number
  documents_checked: number
  documents_with_issues: number
  orphaned_template_refs: number
  orphaned_term_refs: number
  inactive_template_refs: number
  orphaned_document_refs: number
  orphaned_file_refs: number
  identity_hash_mismatches: number
}

/**
 * The outcome of a namespace validation job.
 *
 * Findings do not fail the job — the check ran, and its answer is the
 * deliverable. `issues` is a capped sample; `issues_truncated` says how many
 * more there were, since a namespace with a systematic problem produces one
 * issue per document.
 */
export interface NamespaceIntegrityResult {
  status: 'healthy' | 'warning' | 'error'
  summary: NamespaceIntegritySummary
  issues: NamespaceIntegrityIssue[]
  issues_truncated: number
}

/** Query parameters for `POST /backup/namespaces/{namespace}/validate`. */
export interface ValidateNamespaceParams {
  /** Check term references (one cached lookup per distinct term). */
  check_term_refs?: boolean
  /** Recompute each document's identity hash and compare it to the stored one. */
  check_identity?: boolean
  /** Stop after this many documents (0 = all). */
  limit?: number
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
 * `'restore'` and `'merge'` are ID-preserving and write each namespace in
 * the archive back to itself (a merge may target a differently-named
 * namespace). `'fresh'` re-mints every identity: it takes
 * `target_namespace` (single-namespace archive) or `namespace_map`
 * (multi-namespace). `dry_run` is real, and for a merge it is exact: the
 * plan is computed before anything is written, so the report is what a real
 * run would do — and it still fails on what a real run would refuse. The
 * retired toolkit-era params (`register_synonyms`, `continue_on_error`) are
 * gone from this type — the endpoint 400s when they are set.
 *
 * The clash policies apply to `mode: 'merge'` only. Sending a non-default
 * one with a plain restore is a 400 rather than a silent no-op: a restore
 * requires an empty target, so nothing can clash, and quietly accepting the
 * option would misreport what ran.
 */
export interface RestoreOptions {
  mode?: RestoreMode
  /**
   * Where to write. Required for `mode: 'fresh'` on a single-namespace
   * archive, which is placing new identities somewhere. For the other modes
   * the archive manifest decides, except that a merge may use it to write
   * into a differently-named namespace.
   */
  target_namespace?: string
  /**
   * Fresh only — explicit `{source: target}` for EVERY namespace in a
   * multi-namespace archive; there is no implicit default, because an
   * unmapped namespace restored to its old name would collide with the live
   * original. Several sources may share one target (Registry-key collisions
   * between them refuse at plan time); a target may equal its source name
   * only when that namespace is absent. Pass exactly one of this or
   * `target_namespace` for `'fresh'`.
   */
  namespace_map?: Record<string, string>
  /** Merge only — resolution for a document identity the target already holds. */
  on_clash?: ClashPolicy
  /**
   * Merge only — insert terminologies and templates the target does not
   * have. Without it a missing definition refuses the merge: changing a live
   * namespace's definitions is an active decision, not a side effect of
   * restoring data into it.
   */
  add_missing?: boolean
  /** Merge only — add terms the target's terminology is missing. */
  extend_terminologies?: boolean
  skip_documents?: boolean
  skip_files?: boolean
  batch_size?: number
  dry_run?: boolean
  /**
   * Drop a stale reporting schema for the target namespace before
   * restoring instead of refusing. A dry run reports the would-drop only.
   * Rejected for a merge: its target is live, so a populated reporting
   * schema is expected and dropping it would discard the data being merged
   * into.
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
