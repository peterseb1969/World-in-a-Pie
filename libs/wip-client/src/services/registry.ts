import { BaseService } from './base.js'
import type {
  Namespace,
  NamespaceStats,
  CreateNamespaceRequest,
  UpdateNamespaceRequest,
  RegistryEntryListResponse,
  RegistryLookupResponse,
  RegistryByTermHit,
  RegistryEntryFull,
  RegistryBrowseParams,
  RegistrySearchResponse,
  RegistrySearchParams,
  AddSynonymRequest,
  RemoveSynonymRequest,
  MergeRequest,
  ExportResponse,
  ImportResponse,
  Grant,
  CreateGrantRequest,
  RevokeGrantRequest,
  GrantBulkResponse,
  GrantRevokeBulkResponse,
  APIKeyInfo,
  APIKeyRevokeResult,
  AddAPIKeyNamespaceRequest,
  NamespaceReservation,
  RemoveAPIKeyNamespaceResult,
  APIKeyListResponse,
  CreateAPIKeyRequest,
  CreateAPIKeyResponse,
  ListAPIKeysParams,
  UpdateAPIKeyRequest,
  ActivateGrantRequest,
  GrantActivateBulkResponse,
  GrantListResponse,
  ListAllGrantsParams,
  Attachment,
  AttachmentRequest,
  AttachmentBulkResponse,
  AttachmentListResponse,
  ListAttachmentsParams,
  SystemArchive,
  SystemBackupOptions,
  SystemRestoreOptions,
  SystemRestoreReport,
} from '../types/registry.js'

export class RegistryService extends BaseService {
  constructor(transport: import('../http.js').FetchTransport) {
    super(transport, '/api/registry')
  }

  // ---- Namespaces ----

  async listNamespaces(includeArchived: boolean = false): Promise<Namespace[]> {
    return this.get('/namespaces', { include_archived: includeArchived })
  }

  async getNamespace(prefix: string): Promise<Namespace> {
    return this.get(`/namespaces/${prefix}`)
  }

  async getNamespaceStats(prefix: string): Promise<NamespaceStats> {
    return this.get(`/namespaces/${prefix}/stats`)
  }

  async createNamespace(data: CreateNamespaceRequest): Promise<Namespace> {
    return this.post('/namespaces', data)
  }

  async updateNamespace(
    prefix: string,
    data: UpdateNamespaceRequest,
  ): Promise<Namespace> {
    return this.put(`/namespaces/${prefix}`, data)
  }

  /**
   * Upsert a namespace — create if missing, update if existing.
   *
   * Equivalent to `updateNamespace` but communicates intent: callers
   * (typically app bootstrap scripts) want a single self-healing call
   * that succeeds whether the namespace already exists or not. On
   * create, any field not supplied uses the platform default
   * (isolation_mode='open', deletion_mode='retain', etc).
   */
  async upsertNamespace(
    prefix: string,
    data: UpdateNamespaceRequest,
  ): Promise<Namespace> {
    return this.put(`/namespaces/${prefix}`, data)
  }

  async archiveNamespace(prefix: string, archivedBy?: string): Promise<Namespace> {
    return this.post(`/namespaces/${prefix}/archive`, null, archivedBy ? { archived_by: archivedBy } : undefined)
  }

  async restoreNamespace(prefix: string, restoredBy?: string): Promise<Namespace> {
    return this.post(`/namespaces/${prefix}/restore`, null, restoredBy ? { restored_by: restoredBy } : undefined)
  }

  async deleteNamespace(prefix: string, deletedBy?: string): Promise<void> {
    return this.del(`/namespaces/${prefix}`, undefined, { confirm: true, deleted_by: deletedBy })
  }

  async initializeWipNamespace(): Promise<Namespace> {
    return this.post('/namespaces/initialize-wip')
  }

  // ---- Entries ----

  async listEntries(params?: RegistryBrowseParams): Promise<RegistryEntryListResponse> {
    return this.get('/entries', params)
  }

  async lookupEntry(entryId: string): Promise<RegistryLookupResponse> {
    const resp = await this.post<{ results: RegistryLookupResponse[] }>(
      '/entries/lookup/by-id',
      [{ entry_id: entryId }],
    )
    return resp.results[0]
  }

  /**
   * Free-text search across composite key values (CASE-572, breaking in 0.28.0).
   *
   * Returns `{ hits, total }`: `total` is the full server-side match count
   * even when `limit` bounds the returned hits. `limit` requires a backend
   * that accepts it (registry rejects unknown fields with 422 — ships
   * together with this client change).
   */
  async searchEntries(term: string, options?: {
    namespaces?: string[]
    entityTypes?: string[]
    includeInactive?: boolean
    limit?: number
  }): Promise<{ hits: RegistryByTermHit[]; total: number }> {
    // CASE-568 follow-on: the route is /api/registry/search/by-term — under
    // the search router, NOT /entries. The old '/entries/search/by-term'
    // path 404'd on every call.
    const resp = await this.post<{ results: Array<{ results: RegistryByTermHit[]; total_matches: number }> }>(
      '/search/by-term',
      [{
        term,
        restrict_to_namespaces: options?.namespaces,
        restrict_to_entity_types: options?.entityTypes,
        include_inactive: options?.includeInactive ?? false,
        ...(options?.limit !== undefined ? { limit: options.limit } : {}),
      }],
    )
    const first = resp.results[0]
    return { hits: first?.results ?? [], total: first?.total_matches ?? 0 }
  }

  async unifiedSearch(params: RegistrySearchParams): Promise<RegistrySearchResponse> {
    return this.get('/entries/search', params)
  }

  async getEntry(entryId: string): Promise<RegistryEntryFull> {
    return this.get(`/entries/${entryId}`)
  }

  // ---- Mutations ----

  async addSynonym(request: AddSynonymRequest): Promise<{ status: string; registry_id?: string; error?: string }> {
    const resp = await this.post<{ results: Array<{ status: string; registry_id?: string; error?: string }> }>(
      '/synonyms/add',
      [request],
    )
    return resp.results[0]
  }

  async removeSynonym(request: RemoveSynonymRequest): Promise<{ status: string; registry_id?: string; error?: string }> {
    const resp = await this.post<{ results: Array<{ status: string; registry_id?: string; error?: string }> }>(
      '/synonyms/remove',
      [request],
    )
    return resp.results[0]
  }

  /**
   * Merge two Registry entries (the deprecated entry's id becomes a synonym
   * of the preferred one). Term entries are refused — use
   * `defStore.mergeTerms`, which also moves the absorbed term's value and
   * names, so they resolve to the survivor in validation too.
   */
  async mergeEntries(request: MergeRequest): Promise<{ status: string; preferred_id?: string; deprecated_id?: string; error?: string }> {
    const resp = await this.post<{ results: Array<{ status: string; preferred_id?: string; deprecated_id?: string; error?: string }> }>(
      '/synonyms/merge',
      [request],
    )
    return resp.results[0]
  }

  async deactivateEntry(entryId: string, updatedBy?: string): Promise<{ status: string }> {
    const resp = await this.del<{ results: Array<{ status: string }> }>(
      '/entries',
      [{ entry_id: entryId, updated_by: updatedBy }],
    )
    return resp.results[0]
  }

  // ---- Namespace Export/Import ----

  async exportNamespace(prefix: string, options?: {
    include_files?: boolean
  }): Promise<ExportResponse> {
    return this.post(`/namespaces/${prefix}/export`, null, options)
  }

  async downloadExport(exportId: string): Promise<Blob> {
    return this.getBlob(`/namespaces/exports/${exportId}`)
  }

  async importNamespace(file: Blob, options?: {
    target_prefix?: string
    mode?: 'create' | 'merge' | 'replace'
    imported_by?: string
  }): Promise<ImportResponse> {
    const form = new FormData()
    form.append('file', file)
    return this.postFormData('/namespaces/import', form, options)
  }

  // ---- Grants ----

  async listGrants(prefix: string): Promise<Grant[]> {
    return this.get(`/namespaces/${prefix}/grants`)
  }

  async createGrants(prefix: string, grants: CreateGrantRequest[]): Promise<GrantBulkResponse> {
    return this.post(`/namespaces/${prefix}/grants`, grants)
  }

  async revokeGrants(prefix: string, grants: RevokeGrantRequest[]): Promise<GrantRevokeBulkResponse> {
    return this.del(`/namespaces/${prefix}/grants`, grants)
  }

  /**
   * Make orphaned grants on an existing namespace effective (admin on the
   * namespace). Per item: activated, unchanged (not orphaned), not_found, or
   * error MANAGED_BY_DEPLOYER.
   */
  async activateGrants(prefix: string, grants: ActivateGrantRequest[]): Promise<GrantActivateBulkResponse> {
    return this.post(`/namespaces/${prefix}/grants/activate`, grants)
  }

  /**
   * Grants across every namespace (platform admin), paginated. Orphaned
   * grants usually sit on namespaces that do not exist, which the
   * per-namespace listing cannot reach: list them with `{ orphaned: true }`.
   */
  async listAllGrants(params?: ListAllGrantsParams): Promise<GrantListResponse> {
    return this.get('/grants', params)
  }

  // ---- App attachments (platform admin) ----

  /**
   * Attach apps under the umbrella (bulk upsert by name). Each item REPLACES
   * the record: send the full record, `auth_required` and `allowed_groups`
   * included. A malformed item fails the whole request with 422; per-item
   * refusals are in `results[i].error_code` (see AttachmentBulkResult).
   * Writing an inactive attachment again revives it.
   */
  async upsertAttachments(items: AttachmentRequest[]): Promise<AttachmentBulkResponse> {
    return this.post('/attachments', items)
  }

  /** Deactivate attachments by name; they are kept, never deleted. */
  async deactivateAttachments(names: string[]): Promise<AttachmentBulkResponse> {
    return this.post('/attachments/deactivate', names.map((name) => ({ name })))
  }

  async listAttachments(params?: ListAttachmentsParams): Promise<AttachmentListResponse> {
    return this.get('/attachments', params)
  }

  async getAttachment(name: string): Promise<Attachment> {
    return this.get(`/attachments/${name}`)
  }

  // ---- System backup (platform admin) ----

  /**
   * The system backup: every stored API key (hash only), grant, reservation
   * and attachment. The archive is sensitive (every key hash, the salt):
   * offer it as a download and do not keep it in browser storage.
   *
   * With `namespaces` and/or `attachments` the backup is scoped: it carries
   * those namespaces' grants and reservations, the keys whose namespaces all
   * lie in them and the named attachments, and declares that in
   * `source.scope`. A mirror restore of it removes only within the scope.
   */
  async systemBackup(options: SystemBackupOptions = {}): Promise<SystemArchive> {
    const params: Record<string, string> = {}
    if (options.namespaces?.length) params.namespaces = options.namespaces.join(',')
    if (options.attachments?.length) params.attachments = options.attachments.join(',')
    return Object.keys(params).length ? this.post('/system-backup', null, params) : this.post('/system-backup')
  }

  /**
   * Restore a system backup. A dry run unless `dryRun: false`. Upserts by
   * identity; only `mode: 'mirror'` removes records, so run a mirror as a
   * dry run first, review `deleted` and `refused`, and pass its
   * `plan_digest` as `expect` on the apply. Branch on
   * `skipped[i].reason_code`. A refusal throws WipConflictError whose
   * `wipErrorCode(err)` is SALT_MISMATCH, RESTORE_REFUSED or PLAN_CHANGED.
   */
  async systemRestore(archive: SystemArchive, options: SystemRestoreOptions = {}): Promise<SystemRestoreReport> {
    const params: Record<string, unknown> = {
      dry_run: options.dryRun ?? true,
      include_user_grants: options.includeUserGrants ?? false,
    }
    if (options.mode !== undefined) params.mode = options.mode
    if (options.overwrite !== undefined || options.mode === undefined) {
      params.overwrite = options.overwrite ?? false
    }
    if (options.expect !== undefined) params.expect = options.expect
    return this.post('/system-restore', archive, params)
  }

  // ---- API Keys ----

  /**
   * List API keys with pagination (CASE-335).
   *
   * Breaking change in @wip/client 0.19.0: the response shape is now a
   * `PaginatedResponse<APIKeyInfo>` (envelope with `items`/`total`/`page`/
   * `page_size`/`pages`) instead of a bare `APIKeyInfo[]`. Callers using
   * `.map(...)` on the result must switch to `.items.map(...)`.
   */
  async listAPIKeys(params?: ListAPIKeysParams): Promise<APIKeyListResponse> {
    const qs = new URLSearchParams()
    if (params?.page !== undefined) qs.set('page', String(params.page))
    if (params?.page_size !== undefined) qs.set('page_size', String(params.page_size))
    const suffix = qs.toString() ? `?${qs.toString()}` : ''
    return this.get(`/api-keys${suffix}`)
  }

  async createAPIKey(request: CreateAPIKeyRequest): Promise<CreateAPIKeyResponse> {
    return this.post('/api-keys', request)
  }

  async getAPIKey(name: string): Promise<APIKeyInfo> {
    return this.get(`/api-keys/${name}`)
  }

  async updateAPIKey(name: string, request: UpdateAPIKeyRequest): Promise<APIKeyInfo> {
    return this.patch(`/api-keys/${name}`, request)
  }

  async revokeAPIKey(name: string): Promise<APIKeyRevokeResult> {
    return this.del(`/api-keys/${name}`)
  }

  /** Widen a runtime key to one more namespace, optionally reserving it. */
  async addAPIKeyNamespace(
    name: string,
    prefix: string,
    request: AddAPIKeyNamespaceRequest = {},
  ): Promise<APIKeyInfo> {
    return this.put(`/api-keys/${name}/namespaces/${prefix}`, request)
  }

  /** Narrow a runtime key by one namespace; releases its reservation if the namespace was never created. */
  async removeAPIKeyNamespace(name: string, prefix: string): Promise<RemoveAPIKeyNamespaceResult> {
    return this.del(`/api-keys/${name}/namespaces/${prefix}`)
  }

  /** Every namespace reservation, from the install's file and the Registry (admin only). */
  async listReservations(source?: 'file' | 'runtime'): Promise<NamespaceReservation[]> {
    const suffix = source ? `?source=${source}` : ''
    return this.get(`/reservations${suffix}`)
  }

  /** Release a runtime reservation; the namespace itself is never touched (admin only). */
  async releaseReservation(prefix: string): Promise<{ status: string; prefix: string; app: string }> {
    return this.del(`/reservations/${prefix}`)
  }
}
