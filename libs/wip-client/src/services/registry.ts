import { seg } from '../utils/path.js'
import { BaseService } from './base.js'
import { WipBulkItemError } from '../errors.js'
import type {
  Namespace,
  NamespaceStats,
  CreateNamespaceRequest,
  UpdateNamespaceRequest,
  RegistryEntryListResponse,
  RegistryLookupResponse,
  RegistryByTermHit,
  NamespaceDeletionResult,
  NamespaceDeletionJournal,
  RegistryItemResult,
  SynonymResult,
  MergeResult,
  DeactivateEntryResult,
  RegistryEntryFull,
  RegistryBrowseParams,
  RegistrySearchResponse,
  RegistrySearchParams,
  AddSynonymRequest,
  RemoveSynonymRequest,
  MergeRequest,
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

/**
 * The Registry's single-item mutations answer 200 with a per-item status that
 * may be a refusal (`error`, `forbidden`, `not_found`, `target_not_found`, ...),
 * not only `error`. Anything outside the call's success statuses is thrown.
 */
function expectStatus<T extends RegistryItemResult>(result: T | undefined, ok: string[]): T {
  if (!result) throw new WipBulkItemError('The Registry returned no result', 0, 'missing')
  if (!ok.includes(result.status)) {
    throw new WipBulkItemError(
      result.error || `Registry refused the change: ${result.status}`,
      result.index ?? 0,
      result.status,
      result.error_code ?? undefined,
      result.details ?? undefined,
    )
  }
  return result
}

export class RegistryService extends BaseService {
  constructor(transport: import('../http.js').FetchTransport) {
    super(transport, '/api/registry')
  }

  // ---- Namespaces ----

  async listNamespaces(includeArchived: boolean = false): Promise<Namespace[]> {
    return this.get('/namespaces', { include_archived: includeArchived })
  }

  async getNamespace(prefix: string): Promise<Namespace> {
    return this.get(`/namespaces/${seg(prefix)}`)
  }

  async getNamespaceStats(prefix: string): Promise<NamespaceStats> {
    return this.get(`/namespaces/${seg(prefix)}/stats`)
  }

  async createNamespace(data: CreateNamespaceRequest): Promise<Namespace> {
    return this.post('/namespaces', data)
  }

  async updateNamespace(
    prefix: string,
    data: UpdateNamespaceRequest,
  ): Promise<Namespace> {
    return this.put(`/namespaces/${seg(prefix)}`, data)
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
    return this.put(`/namespaces/${seg(prefix)}`, data)
  }

  async archiveNamespace(prefix: string, archivedBy?: string): Promise<Namespace> {
    return this.post(`/namespaces/${seg(prefix)}/archive`, null, archivedBy ? { archived_by: archivedBy } : undefined)
  }

  async restoreNamespace(prefix: string, restoredBy?: string): Promise<Namespace> {
    return this.post(`/namespaces/${seg(prefix)}/restore`, null, restoredBy ? { restored_by: restoredBy } : undefined)
  }

  /**
   * Delete a namespace and everything in it. The answer says whether the
   * deletion finished: `in_progress` means a step was deferred (finish it with
   * resumeDeletion, or let the Registry's recovery loop), and
   * `completed_with_warnings` means a best-effort step degraded
   * (getDeletionStatus has the per-step errors). `force` proceeds despite
   * references from other namespaces.
   */
  async deleteNamespace(
    prefix: string,
    deletedBy?: string,
    options?: { force?: boolean },
  ): Promise<NamespaceDeletionResult> {
    return this.del(`/namespaces/${seg(prefix)}`, undefined, {
      deleted_by: deletedBy,
      ...(options?.force ? { force: true } : {}),
    })
  }

  /** The deletion journal of a namespace (platform admins only; 404 otherwise). */
  async getDeletionStatus(prefix: string): Promise<NamespaceDeletionJournal> {
    return this.get(`/namespaces/${seg(prefix)}/deletion-status`)
  }

  /** Run every step of an unfinished deletion that is not completed (409 while one runs). */
  async resumeDeletion(prefix: string): Promise<NamespaceDeletionResult> {
    return this.post(`/namespaces/${seg(prefix)}/resume-delete`)
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
    const resp = await this.post<{
      results: Array<{
        status?: string
        error?: string | null
        error_code?: string | null
        results: RegistryByTermHit[]
        total_matches: number
      }>
    }>(
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
    // The Registry answers a failed search (an unknown entity type, an
    // internal error) as a per-item error inside a 200 — never as no hits.
    if (first?.status === 'error') {
      throw new WipBulkItemError(
        first.error || 'Search failed',
        0,
        first.status,
        first.error_code ?? undefined,
      )
    }
    return { hits: first?.results ?? [], total: first?.total_matches ?? 0 }
  }

  async unifiedSearch(params: RegistrySearchParams): Promise<RegistrySearchResponse> {
    return this.get('/entries/search', params)
  }

  async getEntry(entryId: string): Promise<RegistryEntryFull> {
    return this.get(`/entries/${seg(entryId)}`)
  }

  // ---- Mutations ----

  /** Resolves on `added` or `already_exists` (the entry holds it already); throws otherwise. */
  async addSynonym(request: AddSynonymRequest): Promise<SynonymResult> {
    const resp = await this.post<{ results: SynonymResult[] }>('/synonyms/add', [request])
    return expectStatus(resp.results[0], ['added', 'already_exists'])
  }

  /** Resolves on `removed`; throws otherwise (`not_found`, `forbidden`, `error`). */
  async removeSynonym(request: RemoveSynonymRequest): Promise<SynonymResult> {
    const resp = await this.post<{ results: SynonymResult[] }>('/synonyms/remove', [request])
    return expectStatus(resp.results[0], ['removed'])
  }

  /**
   * Merge two Registry entries (the deprecated entry's id becomes a synonym
   * of the preferred one). Term entries are refused — use
   * `defStore.mergeTerms`, which also moves the absorbed term's value and
   * names, so they resolve to the survivor in validation too.
   */
  async mergeEntries(request: MergeRequest): Promise<MergeResult> {
    const resp = await this.post<{ results: MergeResult[] }>('/synonyms/merge', [request])
    return expectStatus(resp.results[0], ['merged'])
  }

  /**
   * Soft-delete a Registry entry: it goes inactive and its canonical id,
   * primary key and synonyms stop resolving. (The resolution-preserving
   * retirement is a privileged service-to-service route, not offered here.)
   * Resolves on `deactivated` or `deleted`; throws otherwise.
   */
  async deactivateEntry(entryId: string, updatedBy?: string): Promise<DeactivateEntryResult> {
    const resp = await this.del<{ results: DeactivateEntryResult[] }>(
      '/entries',
      [{ entry_id: entryId, updated_by: updatedBy }],
    )
    return expectStatus(resp.results[0], ['deactivated', 'deleted'])
  }

  // ---- Grants ----

  async listGrants(prefix: string): Promise<Grant[]> {
    return this.get(`/namespaces/${seg(prefix)}/grants`)
  }

  async createGrants(prefix: string, grants: CreateGrantRequest[]): Promise<GrantBulkResponse> {
    return this.post(`/namespaces/${seg(prefix)}/grants`, grants)
  }

  async revokeGrants(prefix: string, grants: RevokeGrantRequest[]): Promise<GrantRevokeBulkResponse> {
    return this.del(`/namespaces/${seg(prefix)}/grants`, grants)
  }

  /**
   * Make orphaned grants on an existing namespace effective (admin on the
   * namespace). Per item: activated, unchanged (not orphaned), not_found, or
   * error MANAGED_BY_DEPLOYER.
   */
  async activateGrants(prefix: string, grants: ActivateGrantRequest[]): Promise<GrantActivateBulkResponse> {
    return this.post(`/namespaces/${seg(prefix)}/grants/activate`, grants)
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
    return this.get(`/attachments/${seg(name)}`)
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
   * A list that is given is always sent: an empty one is refused by the
   * server (422), never turned into a whole-stack backup.
   */
  async systemBackup(options: SystemBackupOptions = {}): Promise<SystemArchive> {
    const params: Record<string, string> = {}
    // null (a JavaScript caller's "not given") is treated like undefined.
    if (options.namespaces != null) params.namespaces = options.namespaces.join(',')
    if (options.attachments != null) params.attachments = options.attachments.join(',')
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
    return this.get(`/api-keys/${seg(name)}`)
  }

  async updateAPIKey(name: string, request: UpdateAPIKeyRequest): Promise<APIKeyInfo> {
    return this.patch(`/api-keys/${seg(name)}`, request)
  }

  async revokeAPIKey(name: string): Promise<APIKeyRevokeResult> {
    return this.del(`/api-keys/${seg(name)}`)
  }

  /** Widen a runtime key to one more namespace, optionally reserving it. */
  async addAPIKeyNamespace(
    name: string,
    prefix: string,
    request: AddAPIKeyNamespaceRequest = {},
  ): Promise<APIKeyInfo> {
    return this.put(`/api-keys/${seg(name)}/namespaces/${seg(prefix)}`, request)
  }

  /** Narrow a runtime key by one namespace; releases its reservation if the namespace was never created. */
  async removeAPIKeyNamespace(name: string, prefix: string): Promise<RemoveAPIKeyNamespaceResult> {
    return this.del(`/api-keys/${seg(name)}/namespaces/${seg(prefix)}`)
  }

  /** Every namespace reservation, from the install's file and the Registry (admin only). */
  async listReservations(source?: 'file' | 'runtime'): Promise<NamespaceReservation[]> {
    const suffix = source ? `?source=${source}` : ''
    return this.get(`/reservations${suffix}`)
  }

  /** Release a runtime reservation; the namespace itself is never touched (admin only). */
  async releaseReservation(prefix: string): Promise<{ status: string; prefix: string; app: string }> {
    return this.del(`/reservations/${seg(prefix)}`)
  }
}
