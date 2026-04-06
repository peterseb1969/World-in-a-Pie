import { BaseService } from './base.js'
import type {
  Namespace,
  NamespaceStats,
  CreateNamespaceRequest,
  UpdateNamespaceRequest,
  RegistryEntryListResponse,
  RegistryLookupResponse,
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

  async searchEntries(term: string, options?: {
    namespaces?: string[]
    entityTypes?: string[]
    includeInactive?: boolean
  }): Promise<RegistryLookupResponse[]> {
    const resp = await this.post<{ results: Array<{ results: Array<Record<string, unknown>> }> }>(
      '/entries/search/by-term',
      [{
        term,
        restrict_to_namespaces: options?.namespaces,
        restrict_to_entity_types: options?.entityTypes,
        include_inactive: options?.includeInactive ?? false,
      }],
    )
    return (resp.results[0]?.results ?? []) as unknown as RegistryLookupResponse[]
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
}
