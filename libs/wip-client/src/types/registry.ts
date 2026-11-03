import type { PaginatedResponse } from './common.js'

export interface Namespace {
  prefix: string
  description: string
  isolation_mode: 'open' | 'strict'
  /** Who may reference this namespace from another one: 'closed' only writers who can read it. */
  inbound_references: 'closed' | 'open'
  deletion_mode: 'retain' | 'full'
  allowed_external_refs: string[]
  id_config: Record<string, IdAlgorithmConfig>
  status: 'active' | 'archived' | 'deleted'
  created_at: string
  created_by: string | null
  updated_at: string
  updated_by: string | null
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
  updated_by: string | null
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
  source_info: RegistrySourceInfo | null
  source_data: Record<string, unknown> | null
  error: string | null
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
  updated_by: string | null
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
  created_by?: string
}

export interface RemoveSynonymRequest {
  target_id: string
  synonym_namespace: string
  synonym_entity_type: string
  synonym_composite_key: Record<string, unknown>
  updated_by?: string
}

export interface MergeRequest {
  preferred_id: string
  deprecated_id: string
  updated_by?: string
}

// ---- Namespace Export/Import ----

export interface ExportResponse {
  export_id: string
  prefix: string
  download_url: string
  stats: Record<string, number>
}

export interface ImportResponse {
  prefix: string
  mode: 'create' | 'merge' | 'replace'
  stats: Record<string, number>
  source_prefix: string | null
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
}

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
  expires_at?: string
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
