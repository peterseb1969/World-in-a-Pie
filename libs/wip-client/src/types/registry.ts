import type { PaginatedResponse } from './common.js'

export interface Namespace {
  prefix: string
  /**
   * The namespace's identity (UUID7), apart from its name: an ID-preserving
   * restore keeps it; a namespace deleted and created again under its name
   * has a new one.
   */
  namespace_id: string
  description: string
  isolation_mode: 'open' | 'strict'
  /** Who may reference this namespace from another one: 'closed' only writers who can read it. */
  inbound_references: 'closed' | 'open'
  deletion_mode: 'retain' | 'full'
  allowed_external_refs: string[]
  id_config: Record<string, IdAlgorithmConfig>
  /** `locked` while a deletion is under way (shown to platform admins). */
  status: 'active' | 'archived' | 'deleted' | 'locked'
  created_at: string
  created_by: string | null
  updated_at: string
  updated_by: string | null
  /**
   * Set by a create only: the orphaned grants made for this namespace (its
   * namespace_id) that became effective, as 'namespace/subject_type:subject'.
   * Empty otherwise.
   */
  activated_orphan_grants?: string[]
  /** Set by a create only: e.g. an archived namespace_id that could not be adopted. */
  warnings?: string[]
}

export interface NamespaceStats {
  prefix: string
  description: string
  isolation_mode: string
  inbound_references: string
  deletion_mode: string
  status: string
  entity_counts: Record<string, number>
}

export interface IdAlgorithmConfig {
  algorithm: 'uuid7' | 'uuid4' | 'prefixed' | 'nanoid' | 'pattern' | 'any'
  prefix?: string
  pad?: number
  length?: number
  pattern?: string
}

export interface CreateNamespaceRequest {
  prefix: string
  description?: string
  isolation_mode?: 'open' | 'strict'
  inbound_references?: 'closed' | 'open'
  deletion_mode?: 'retain' | 'full'
  allowed_external_refs?: string[]
  id_config?: Record<string, IdAlgorithmConfig>
  created_by?: string
}

export interface UpdateNamespaceRequest {
  description?: string
  isolation_mode?: 'open' | 'strict'
  inbound_references?: 'closed' | 'open'
  deletion_mode?: 'retain' | 'full'
  allowed_external_refs?: string[]
  id_config?: Record<string, IdAlgorithmConfig>
  updated_by?: string
  /**
   * Required to be `true` when flipping `deletion_mode` from 'retain' to
   * 'full' on an existing namespace (enabling hard-delete on namespace
   * deletion). The backend rejects the transition without it. Ignored for
   * other updates, and not needed when creating a namespace directly with
   * `deletion_mode: 'full'`. Safety guard — see CASE-291 / CASE-429.
   */
  confirm_enable_deletion?: boolean
}

export interface RegistryEntry {
  entry_id: string
  namespace: string
  entity_type: string
  primary_composite_key: Record<string, unknown>
  synonyms_count: number
  status: 'active' | 'reserved' | 'inactive'
  created_at: string
  created_by: string | null
  updated_at: string
}

export type RegistryEntryListResponse = PaginatedResponse<RegistryEntry>

export interface RegistrySourceInfo {
  system_id: string
  endpoint_url: string | null
}

export interface RegistrySynonym {
  namespace: string
  entity_type: string
  composite_key: Record<string, unknown>
  composite_key_hash: string
  source_info: RegistrySourceInfo | null
  created_at: string
  created_by: string | null
}

export interface RegistryEntryFull {
  entry_id: string
  namespace: string
  entity_type: string
  primary_composite_key: Record<string, unknown>
  primary_composite_key_hash: string
  synonyms: RegistrySynonym[]
  source_info: RegistrySourceInfo | null
  search_values: string[]
  metadata: Record<string, unknown>
  status: string
  created_at: string
  created_by: string | null
  updated_at: string
  /** The authenticated caller who last changed the entry. */
  updated_by: string | null
  /** The caller's own note on who acted, sent as `updated_by` with that write. */
  actor_note?: string | null
}

/**
 * A single hit from POST /api/registry/search/by-term (CASE-572).
 *
 * Distinct from RegistryLookupResponse: the by-term route returns
 * `registry_id`/`matched_in`, not `entry_id`/`matched_via`.
 */
export interface RegistryByTermHit {
  registry_id: string
  namespace: string
  entity_type: string
  matched_in: 'primary' | 'synonym'
  matched_namespace: string
  matched_entity_type: string
  matched_composite_key: Record<string, unknown>
  all_synonyms: RegistrySynonym[]
}

/**
 * The per-item answer of the Registry's single-item mutations (synonym add and
 * remove, merge, entry deactivation). The client returns it only on success;
 * any other status is thrown as a WipBulkItemError carrying error_code and
 * details.
 */
export interface RegistryItemResult {
  index: number
  status: string
  error?: string | null
  error_code?: string | null
  details?: Record<string, unknown> | null
}

/** `added`, or `already_exists` when the entry already holds the synonym. */
export interface SynonymResult extends RegistryItemResult {
  registry_id?: string | null
}

/** `merged`. */
export interface MergeResult extends RegistryItemResult {
  preferred_id?: string | null
  deprecated_id?: string | null
}

/** `deactivated` or `deleted`. */
export interface DeactivateEntryResult extends RegistryItemResult {
  entry_id?: string | null
}

/**
 * The answer to DELETE /api/registry/namespaces/{prefix} and to
 * POST .../resume-delete. A deletion is finished only when `status` is
 * `completed` or `completed_with_warnings` (a best-effort object-store or
 * reporting step degraded: the per-step errors are in getDeletionStatus).
 * `in_progress` means a step was deferred — the Registry's recovery loop
 * finishes it, or resumeDeletion does.
 */
export interface NamespaceDeletionResult {
  status: 'completed' | 'completed_with_warnings' | 'in_progress' | string
  namespace: string
  /** Present once the deletion is complete. */
  summary?: Record<string, unknown> | null
  /** Present while the deletion is unfinished. */
  steps_completed?: number
  steps_total?: number
}

/** GET /api/registry/namespaces/{prefix}/deletion-status (platform admins only). */
export interface NamespaceDeletionJournal {
  namespace: string
  status: string
  requested_by: string | null
  requested_note: string | null
  requested_at: string | null
  completed_at: string | null
  force: boolean
  /** The first 1,000 references from other namespaces the deletion breaks. */
  broken_references: Record<string, unknown>[]
  /** Every broken reference, counted per kind and source namespace. */
  broken_references_counts?: { type: string; source_namespace: string; count: number }[]
  /** True when broken_references holds only the first 1,000. */
  broken_references_truncated?: boolean
  steps: Record<string, unknown>[]
  summary: Record<string, unknown> | null
}

export interface RegistryLookupResponse {
  index: number
  status: string
  entry_id: string | null
  namespace: string | null
  entity_type: string | null
  matched_namespace: string | null
  matched_entity_type: string | null
  matched_composite_key: Record<string, unknown> | null
  matched_via: string | null
  synonyms: RegistrySynonym[]
  /** Stored source details, as data: the Registry never requests endpoint_url itself. */
  source_info: RegistrySourceInfo | null
  /** When status is 'ambiguous': the entries the value resolves to. */
  candidates?: RegistryLookupCandidate[]
  error: string | null
  error_code?: string | null
}

/** One entry a value resolves to, with the key that carried it. */
export interface RegistryLookupCandidate {
  entry_id: string
  namespace: string
  entity_type: string
  matched_via: 'entry_id' | 'primary_key' | 'synonym'
  matched_composite_key: Record<string, unknown> | null
}

export interface RegistryBrowseParams {
  namespace?: string
  entity_type?: string
  status?: string
  q?: string
  page?: number
  page_size?: number
}

export interface RegistrySearchResult {
  entry_id: string
  namespace: string
  entity_type: string
  status: string
  primary_composite_key: Record<string, unknown>
  synonyms: RegistrySynonym[]
  source_info: RegistrySourceInfo | null
  metadata: Record<string, unknown>
  created_at: string
  created_by: string | null
  updated_at: string
  /** The authenticated caller who last changed the entry. */
  updated_by: string | null
  /** The caller's own note on who acted, sent as `updated_by` with that write. */
  actor_note?: string | null
  matched_via: 'entry_id' | 'composite_key_value' | 'synonym_key_value'
  matched_value: string
  resolution_path: string
}

export interface RegistrySearchResponse {
  items: RegistrySearchResult[]
  total: number
  page: number
  page_size: number
  query: string
}

export interface RegistrySearchParams {
  q: string
  namespace?: string
  entity_type?: string
  status?: string
  page?: number
  page_size?: number
}

export interface AddSynonymRequest {
  target_id: string
  synonym_namespace: string
  synonym_entity_type: string
  synonym_composite_key: Record<string, unknown>
  synonym_source_info?: { system_id: string; endpoint_url?: string }
  /** A note on who acted, kept as the entry's `actor_note`; the synonym's `created_by` is the authenticated caller. */
  created_by?: string
}

export interface RemoveSynonymRequest {
  target_id: string
  synonym_namespace: string
  synonym_entity_type: string
  synonym_composite_key: Record<string, unknown>
  /** A note on who acted, kept as the entry's `actor_note`; the entry's `updated_by` is the authenticated caller. */
  updated_by?: string
}

export interface MergeRequest {
  preferred_id: string
  deprecated_id: string
  /** A note on who acted, kept as the entry's `actor_note`; the entry's `updated_by` is the authenticated caller. */
  updated_by?: string
}

// ---- Grants ----

export type GrantSubjectType = 'user' | 'api_key' | 'group'
export type GrantPermission = 'read' | 'write' | 'admin'

/**
 * The error code the Registry answers for any API write to a record
 * wip-deploy owns (a key, grant or reservation imported from the install's
 * files): HTTP 409 `{detail, error_code}` on single writes, a per-item
 * `error_code` on bulk grant writes. Change such a record with wip-deploy.
 */
export const MANAGED_BY_DEPLOYER = 'managed_by_deployer'

/** Who wrote a Registry auth record. */
export type RecordOrigin = 'file' | 'console' | 'restored'

export interface Grant {
  namespace: string
  subject: string
  subject_type: GrantSubjectType
  permission: GrantPermission
  granted_by: string
  granted_at: string
  expires_at: string | null
  /**
   * 'wip-deploy' for a grant imported from the install's key file; the API
   * refuses to change it (MANAGED_BY_DEPLOYER). Absent from Registries that
   * predate it.
   */
  managed_by?: 'wip-deploy' | null
  /**
   * True for a grant a system restore wrote while its namespace did not
   * exist (and was not reserved by the archive's owner). An orphan counts as
   * no grant until it is activated (`activateGrants`), granted again, or the
   * namespace is created by a platform admin. A key whose own namespaces
   * list names the prefix still reads it, as any such key does. Absent from
   * Registries that predate it.
   */
  orphaned?: boolean
  orphaned_at?: string | null
}

/** Names an orphaned grant to activate. */
export interface ActivateGrantRequest {
  subject: string
  subject_type?: GrantSubjectType
}

export interface GrantActivateResult {
  index: number
  /** 'unchanged': the grant is not orphaned. */
  status: 'activated' | 'unchanged' | 'not_found' | 'error'
  subject: string
  error?: string | null
  /** For an error item: MANAGED_BY_DEPLOYER. */
  error_code?: string | null
}

export interface GrantActivateBulkResponse {
  results: GrantActivateResult[]
  total: number
  succeeded: number
  failed: number
}

/** Query params for `GET /api/registry/grants` (platform admin). */
export interface ListAllGrantsParams {
  /** true: only orphaned grants; false: only effective ones. */
  orphaned?: boolean
  namespace?: string
  page?: number
  /** Default 50, max 1000. */
  page_size?: number
}

export type GrantListResponse = PaginatedResponse<Grant>

export interface CreateGrantRequest {
  subject: string
  subject_type?: GrantSubjectType
  permission?: GrantPermission
  expires_at?: string
}

export interface RevokeGrantRequest {
  subject: string
  subject_type?: GrantSubjectType
}

export interface GrantBulkResult {
  index: number
  status: 'created' | 'updated' | 'error'
  subject: string
  permission: string | null
  error: string | null
  /** Machine-readable reason for an error item, e.g. MANAGED_BY_DEPLOYER. */
  error_code?: string | null
}

export interface GrantBulkResponse {
  results: GrantBulkResult[]
  total: number
  succeeded: number
  failed: number
}

export interface GrantRevokeResult {
  index: number
  status: 'revoked' | 'not_found' | 'error'
  subject: string
  /** For an error item: the reason. */
  error?: string | null
  /** For an error item: e.g. MANAGED_BY_DEPLOYER. */
  error_code?: string | null
}

export interface GrantRevokeBulkResponse {
  results: GrantRevokeResult[]
  total: number
  succeeded: number
  failed: number
}

// ---- API Keys ----

export interface APIKeyInfo {
  name: string
  owner: string
  groups: string[]
  description: string | null
  created_at: string
  expires_at: string | null
  enabled: boolean
  namespaces: string[] | null
  created_by: string
  source: 'config' | 'runtime'
  /**
   * Config-declared namespace grants ({namespace: read|write|admin}).
   * Always null for runtime keys — their write authority is Registry
   * NamespaceGrants, not shown here (CASE-693).
   */
  grants: Record<string, 'read' | 'write' | 'admin'> | null
  /**
   * For a runtime key: the config key that has the same plaintext. A
   * shadowed runtime key does not authenticate (the plaintext is the config
   * key's) and is left out of key sync. Always null for config keys; absent
   * from Registries that predate it.
   */
  shadowed_by?: string | null
  /** For a runtime key: the namespaces it reserves. Null for config keys. */
  reserved_namespaces?: string[] | null
  /**
   * 'wip-deploy' when the Registry holds the key as a record imported from
   * the install's key file. The API refuses to change it
   * (MANAGED_BY_DEPLOYER); change it with wip-deploy. Absent from
   * Registries that predate it.
   */
  managed_by?: 'wip-deploy' | null
  /** For a stored record: who wrote it. */
  origin?: RecordOrigin | null
}

/**
 * Settings a reserved namespace is created with. Omitted fields take the
 * platform defaults. The key's bootstrap may omit them or send equal values,
 * never different ones.
 */
export interface ReservationSettings {
  description?: string
  isolation_mode?: 'open' | 'strict'
  allowed_external_refs?: string[]
  inbound_references?: 'closed' | 'open'
  id_config?: Record<string, unknown>
  deletion_mode?: 'retain' | 'full'
}

/** A namespace a runtime key reserves. */
export interface NamespaceReserve {
  prefix: string
  settings?: ReservationSettings
}

/** Body of addAPIKeyNamespace (widen a runtime key by one namespace). */
export interface AddAPIKeyNamespaceRequest {
  /** The grant on the namespace: 'admin' when reserving, else 'read' by default. */
  permission?: 'read' | 'write' | 'admin'
  /** Reserve the namespace with these settings (`{}` for the defaults). */
  reserve?: ReservationSettings
}

/** Result of removeAPIKeyNamespace (narrow a runtime key by one namespace). */
export interface RemoveAPIKeyNamespaceResult {
  name: string
  prefix: string
  namespaces: string[]
  /** 'released', 'kept' (the namespace exists), or null (it was not reserved by this key). */
  reservation: 'released' | 'kept' | null
}

/** A namespace reservation: the install's (imported from its file) or a runtime key's. */
export interface NamespaceReservation {
  prefix: string
  /** The owning app (file) or runtime API key (runtime). */
  app: string
  source: 'file' | 'runtime'
  /** The settings a create applies. */
  settings: Record<string, unknown>
  namespace_exists: boolean
  /**
   * 'wip-deploy' for a reservation imported from the install's file; the
   * API refuses to release it (MANAGED_BY_DEPLOYER). Absent from Registries
   * that predate it.
   */
  managed_by?: 'wip-deploy' | null
  /**
   * Always null from current Registries: one record holds each prefix, and
   * a file reservation of a prefix a runtime one held replaces it. Older
   * Registries set it when both sources reserved the prefix.
   */
  conflict: string | null
}

/** Result of revoking a runtime API key. */
export interface APIKeyRevokeResult {
  status: string
  name: string
  /** Namespaces whose grants to the key were revoked with it. */
  revoked_grants: string[]
  /** Reservations released because their namespace was never created. */
  released_reservations?: string[]
  /** Reservations kept because their namespace exists; release with releaseReservation. */
  kept_reservations?: string[]
}

export interface CreateAPIKeyRequest {
  name: string
  owner?: string
  groups?: string[]
  namespaces?: string[] | null
  description?: string
  expires_at?: string
  /**
   * CASE-450: also create a namespace grant at this level for the new key
   * (subject = key name) on each namespace in `namespaces`. Without it a
   * scoped key can read its namespaces but not write.
   */
  grant_permission?: 'read' | 'write' | 'admin'
  /**
   * Namespaces the key reserves (each listed in `namespaces`). The key gets
   * admin on each and can create it with the reserved settings: an app key
   * for an app wip-deploy does not manage.
   */
  reserve?: NamespaceReserve[]
}

export interface CreateAPIKeyResponse extends APIKeyInfo {
  plaintext_key: string
  /** Namespaces a grant was created on (CASE-450 grant_permission). */
  granted_namespaces?: string[] | null
}

export interface UpdateAPIKeyRequest {
  description?: string
  groups?: string[]
  namespaces?: string[] | null
  /**
   * UTC (a value without an offset is read as UTC). `null` removes the
   * expiry (the key never expires); left out, it is unchanged; a time already
   * past is refused (422).
   */
  expires_at?: string | null
  enabled?: boolean
}

/**
 * Paginated list response from `GET /api/registry/api-keys` (CASE-335).
 * Follows the platform-wide pagination envelope (see `wip://conventions`).
 */
export type APIKeyListResponse = PaginatedResponse<APIKeyInfo>

/** Query params for `GET /api/registry/api-keys` (CASE-335). */
export interface ListAPIKeysParams {
  /** Default 1. */
  page?: number
  /** Default 50, capped at 100. */
  page_size?: number
}

// ---- App attachments ----

export interface UpstreamTLS {
  /** Verify the upstream's certificate (https upstreams). Default true. */
  verify?: boolean
  /** A CA certificate (PEM content) the upstream's certificate chains to. */
  ca_pem?: string | null
}

/**
 * An app attached under the umbrella, served at the root of its own origin,
 * `https://<name>.<hostname>[:port]/`, by the router's app edge: every path
 * goes to the upstream as the browser sent it. A write REPLACES the whole record: `auth_required` and
 * `allowed_groups` are required on every write (no defaults), so send the
 * full record you read. A missing or ill-typed field fails the whole request
 * with 422 (WipValidationError), not per item. Platform admin only.
 */
export interface AttachmentRequest {
  /** `^[a-z][a-z0-9-]{1,40}$`; the app's origin is `<name>.<hostname>`. */
  name: string
  /** Scheme, host and optional port only: no path, credentials, query or fragment. */
  upstream: string
  auth_required: boolean
  /** Groups the gateway lets through; [] = any logged-in user. Needs auth_required. */
  allowed_groups: string[]
  upstream_host_header?: boolean
  streaming?: boolean
  upstream_tls?: UpstreamTLS
  display_name?: string
  ui_only?: boolean
  /** A path under the upstream the app edge may probe. */
  health?: string | null
}

export interface Attachment extends Required<Omit<AttachmentRequest, 'upstream_tls' | 'health'>> {
  status: 'active' | 'inactive'
  upstream_tls: UpstreamTLS
  health: string | null
  managed_by: 'wip-deploy' | null
  created_at: string
  created_by: string
  updated_at: string
  updated_by: string
  /**
   * The install is configured to serve this attachment: it is active and an
   * app edge runs. Not a reachability check (the Registry never calls an
   * upstream). Absent from Registries older than this field.
   */
  served?: boolean
  /** Why `served` is false; null when served. */
  not_served_reason?: AttachmentNotServedReason | null
}

/**
 * `inactive`: the record is deactivated. `name_invalid`: a record stored while
 * a name could end with a hyphen; no origin can carry it and the app edge
 * refuses it. `upstream_invalid`: a record stored
 * before the Registry's upstream host rule; the app edge refuses its host.
 * `no_app_edge`: this install runs no app edge. `policy_unavailable`: the
 * attach policy file cannot be used, so the edge serves nothing.
 */
export type AttachmentNotServedReason =
  | 'inactive'
  | 'name_invalid'
  | 'upstream_invalid'
  | 'no_app_edge'
  | 'policy_unavailable'

/**
 * Per-item error codes of attachment writes (`error_code`, with
 * `details.rule` where noted): `validation_failed` (rule `upstream_invalid`,
 * `policy_unavailable` or `duplicate_name`), `forbidden` (rule
 * `upstream_blocked`), `name_collision` (the label of one of the install's
 * own apps), MANAGED_BY_DEPLOYER, `concurrency_conflict` (retry), `not_found`
 * (deactivate only), `invalid_state` (rule `no_app_edge`: the install runs
 * no app edge, so every write is refused; deactivating still works).
 */
export interface AttachmentBulkResult {
  index: number
  status: 'created' | 'updated' | 'unchanged' | 'deactivated' | 'error'
  name?: string | null
  /** e.g. the upstream does not resolve from the Registry's network yet. */
  warnings?: string[]
  error?: string | null
  error_code?: string | null
  details?: { rule?: string } | null
}

export interface AttachmentBulkResponse {
  results: AttachmentBulkResult[]
  total: number
  succeeded: number
  failed: number
}

export interface ListAttachmentsParams {
  status?: 'active' | 'inactive'
  page?: number
  /** Default 50, max 1000. */
  page_size?: number
}

export type AttachmentListResponse = PaginatedResponse<Attachment>

// ---- System backup ----

/**
 * The system backup archive: every stored API key (bcrypt hash, never a
 * plaintext), grant, reservation and app attachment. It is SENSITIVE: it
 * holds every key hash and the runtime-key salt. Do not keep it in browser
 * storage; hand it to the user as a file and drop it.
 */
export interface SystemArchive {
  format: 'wip-system-backup'
  /**
   * 2: every read a key has is a grant in the archive. 1: made while a
   * runtime key's namespaces list gave read by itself (or by an unreleased
   * develop build of 2026-11-08 to 2026-11-13, which already read through
   * grants only); a restore writes that read as grants (the report's
   * `scope_read_grants`).
   */
  version: 1 | 2
  created_at: string
  source: {
    oidc_issuer: string | null
    salt_fingerprint: string
    salt?: string | null
    /** Set by a scoped backup; absent means the whole stack. */
    scope?: SystemArchiveScope | null
  }
  api_keys: Record<string, unknown>[]
  grants: Record<string, unknown>[]
  reservations: Record<string, unknown>[]
  attachments: Record<string, unknown>[]
}

/**
 * What a scoped system backup covers, declared in the archive. A mirror of a
 * scoped archive removes only within it. Grants and reservations are in
 * scope by namespace; a key only when every namespace it holds is in scope;
 * an attachment only when named.
 */
export interface SystemArchiveScope {
  namespaces: string[]
  attachments: string[]
  /**
   * Keys the scope touches but cannot carry (a namespace outside it, or
   * none): those with a grant or reservation in scope, or namespaces that
   * overlap it. A scoped mirror never removes them.
   */
  keys_left_out: string[]
}

/**
 * Options of `systemBackup`. Either one makes the backup scoped. A list that
 * is given must not be empty: the server refuses an empty one (422) rather
 * than back up the whole stack.
 */
export interface SystemBackupOptions {
  namespaces?: string[]
  /** Attachments have no namespace: a scoped backup carries only those named. */
  attachments?: string[]
}

/** Why a restore left a record out. Branch on this, not on `reason`. */
export type SystemRestoreSkipReason =
  | 'managed_by_deployer'
  | 'managed_on_source'
  | 'invalid_record'
  | 'differs'
  | 'orphaned_here'
  | 'orphaned_in_archive'
  | 'other_oidc_issuer'
  | 'config_key_name'
  | 'app_route'
  | 'attach_policy_unavailable'
  /** An active attachment, skipped: the install runs no app edge to serve it. */
  | 'no_app_edge'
  | 'upstream_invalid'
  | 'upstream_blocked'
  | 'concurrent_write'
  /** Mirror: a grant to a key the archive lacks, which the mirror removes. */
  | 'mirror_removes_key'
  /**
   * In `refused`: the restore would remove (mirror) or replace by the
   * archive's different record (overwrite, mirror) the key this request
   * authenticates with.
   */
  | 'caller_key'
  /** A scoped archive's record outside the scope it declares (never written). */
  | 'outside_archive_scope'
  /** A scoped restore would replace a key that, here, reaches outside the scope. */
  | 'out_of_scope_here'
  /**
   * Version 1 archive: a read grant a key's namespaces list stood in for, on
   * a namespace the archive records no id for and that is not here (or is
   * only an install's reservation, which holds no id yet). Restore again
   * after the namespace is restored.
   */
  | 'scope_namespace_absent'
  /**
   * Version 1 archive: a read grant a key's namespaces list stood in for,
   * for a key the restore does not leave as the archive's (one of this
   * install's that shares the name).
   */
  | 'scope_key_not_restored'
  /**
   * A grant archived with no namespace id (no namespace of its name existed
   * on the source), where a namespace of the name exists here: not the one
   * it was given for.
   */
  | 'unrelated_namespace_here'

export interface SystemRestoreSkipped {
  /** Key or attachment name, reservation prefix, or 'namespace/subject_type:subject'. */
  id: string
  /** For people. */
  reason: string
  reason_code: SystemRestoreSkipReason
  /** Key or attachment. */
  name?: string | null
  /** Reservation. */
  prefix?: string | null
  /** Grant. */
  namespace?: string | null
  subject_type?: GrantSubjectType | null
  subject?: string | null
}

export interface SystemRestoreRecordReport {
  created: string[]
  replaced: string[]
  unchanged: string[]
  skipped: SystemRestoreSkipped[]
  /** Grants only: those of created/replaced written orphaned. */
  orphaned: string[]
  /** Attachments only: those whose upstream does not resolve from the Registry yet. */
  unresolved: string[]
  /** Mirror only: keys, grants or reservations the archive lacks, removed (or, in a dry run, to be). */
  deleted?: string[]
  /** Mirror only, attachments: active ones the archive lacks, deactivated (never deleted). */
  deactivated?: string[]
}

/**
 * upsert (default): create what is missing. overwrite: also replace what
 * differs. mirror: also remove what the archive does not hold, so the
 * archive becomes the exact auth state (records the install's files manage,
 * config-key names, the bootstrap grants, user grants the archive's issuer
 * would not restore, and the calling key are kept).
 */
export type SystemRestoreMode = 'upsert' | 'overwrite' | 'mirror'

export interface SystemRestoreReport {
  dry_run: boolean
  /** Absent from Registries that predate restore modes. */
  mode?: SystemRestoreMode
  /** The archive's runtime-key salt replaced this stack's (or would, in a dry run). */
  salt_adopted: boolean
  warnings: string[]
  /**
   * Overwrite and mirror: what the restore must not do (remove, or replace
   * by the archive's different record, the key this request authenticates
   * with). A plan with refusals is not applied: the apply throws with
   * RESTORE_REFUSED.
   */
  refused?: SystemRestoreSkipped[]
  /** Mirror only: pass it as `expect` on the apply (see SystemRestoreOptions). */
  plan_digest?: string | null
  /**
   * Version 1 archives: the read grants written (in a dry run, to be
   * written) for a key's namespaces list, also listed under `grants` (and
   * `grants.orphaned` when written orphaned); one not written is under
   * `grants.skipped`. Absent from Registries that predate archive version 2.
   */
  scope_read_grants?: string[]
  reservations: SystemRestoreRecordReport
  api_keys: SystemRestoreRecordReport
  grants: SystemRestoreRecordReport
  attachments: SystemRestoreRecordReport
}

export interface SystemRestoreOptions {
  /** Default true: report only. */
  dryRun?: boolean
  /** Default 'upsert'. Run a mirror as a dry run first and review `deleted`. */
  mode?: SystemRestoreMode
  /**
   * Mirror apply: the dry run's `plan_digest`. The apply is refused
   * (PLAN_CHANGED) when what it would remove has changed since.
   */
  expect?: string
  /** The same as mode 'overwrite'; kept for existing callers. Not with mode 'upsert'. */
  overwrite?: boolean
  /** Restore user grants although the archive comes from another OIDC issuer. */
  includeUserGrants?: boolean
}

/**
 * The 409 `error_code` of a system restore whose archive was made under a
 * runtime-key salt this stack cannot take. Today that is a stack holding
 * API keys of its own, or an archive carrying only the salt's fingerprint;
 * a stack without keys of its own adopts the archive's salt instead. The
 * rule may widen; branch on the code, not on the message.
 */
export const SALT_MISMATCH = 'salt_mismatch'

/**
 * 409 `error_code` of an apply that would remove or replace the calling key
 * (see the report's `refused`). Nothing is written.
 */
export const RESTORE_REFUSED = 'restore_refused'

/** 409 `error_code` of a mirror apply whose plan changed since the dry run given as `expect`. */
export const PLAN_CHANGED = 'plan_changed'
