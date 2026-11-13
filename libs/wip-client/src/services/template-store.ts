import { seg } from '../utils/path.js'
import { BaseService } from './base.js'
import type { BulkResponse, BulkResultItem } from '../types/common.js'
import type {
  Template,
  TemplateListResponse,
  CreateTemplateRequest,
  UpdateTemplateRequest,
  ValidateTemplateRequest,
  ValidateTemplateResponse,
  ActivateTemplateResponse,
  CascadeResponse,
  EndpointRefInput,
} from '../types/template.js'

export class TemplateStoreService extends BaseService {
  constructor(transport: import('../http.js').FetchTransport) {
    super(transport, '/api/template-store')
  }

  // ---- Templates ----

  async listTemplates(params?: {
    page?: number
    page_size?: number
    status?: string
    extends?: string
    value?: string
    latest_only?: boolean
    namespace?: string
    sort_by?: string
    sort_order?: 'asc' | 'desc'
  }): Promise<TemplateListResponse> {
    return this.get('/templates', params)
  }

  async getTemplate(id: string, version?: number): Promise<Template> {
    return this.get(`/templates/${seg(id)}`, version ? { version } : undefined)
  }

  async getTemplateRaw(id: string, version?: number): Promise<Template> {
    return this.get(`/templates/${seg(id)}/raw`, version ? { version } : undefined)
  }

  async getTemplateByValue(value: string, opts?: { namespace?: string }): Promise<Template> {
    // A template `value` is unique only within a namespace; with a cross-namespace
    // (admin) key, omitting `namespace` returns the latest across ALL namespaces.
    // Pass `namespace` to scope (CASE-496). Backend: GET /templates/by-value/{value}.
    return this.get(`/templates/by-value/${seg(value)}`, opts?.namespace ? { namespace: opts.namespace } : undefined)
  }

  async getTemplateByValueRaw(value: string, namespace: string): Promise<Template> {
    return this.get(`/templates/by-value/${seg(value)}/raw?namespace=${encodeURIComponent(namespace)}`)
  }

  async getTemplateVersions(value: string, opts?: { namespace?: string }): Promise<TemplateListResponse> {
    // Without `namespace`, a cross-namespace key gets every namespace's versions of
    // this value (a `value` is unique only within a namespace). Pass `namespace` to
    // scope (CASE-496). Backend: GET /templates/by-value/{value}/versions.
    return this.get(`/templates/by-value/${seg(value)}/versions`, opts?.namespace ? { namespace: opts.namespace } : undefined)
  }

  async getTemplateByValueAndVersion(value: string, version: number, opts?: { namespace?: string }): Promise<Template> {
    // A `value` is unique only within a namespace; pass `namespace` to disambiguate
    // (CASE-497). Backend: GET /templates/by-value/{value}/versions/{version}.
    return this.get(`/templates/by-value/${seg(value)}/versions/${seg(version)}`, opts?.namespace ? { namespace: opts.namespace } : undefined)
  }

  async getTemplateVersionsById(templateId: string): Promise<TemplateListResponse> {
    // A template_id is globally unique and stable across versions, so no namespace
    // is needed (CASE-497). Backend: GET /templates/{template_id}/versions.
    return this.get(`/templates/${seg(templateId)}/versions`)
  }

  /**
   * Create a template, or version it: template create is an upsert keyed on
   * (namespace, value), so a re-run bootstrap is safe.
   *
   * - A new value is `created` (version 1).
   * - An identical re-post is `unchanged` (the existing id and version).
   * - Any other difference is `updated`: version N+1, with the schema diff
   *   in `details` (`added_optional`, `added_required`, `removed`, ...).
   * - Refused, as a thrown `WipBulkItemError`: a different identity_fields
   *   (`identity_fields_immutable`; changing identity is a new template
   *   value), a changed usage / versioned / reporting table name / edge
   *   endpoints (`immutable_property`), and a draft onto an existing value
   *   (`already_exists`).
   *
   * @param options.onConflict - Deprecated: accepted, but both values upsert.
   */
  async createTemplate(
    data: CreateTemplateRequest,
    options?: { onConflict?: 'error' | 'validate' },
  ): Promise<BulkResultItem> {
    const params = options?.onConflict ? { on_conflict: options.onConflict } : undefined
    return this.bulkWriteOne('/templates', data, 'POST', params)
  }

  async createTemplates(
    data: CreateTemplateRequest[],
    options?: { onConflict?: 'error' | 'validate' },
  ): Promise<BulkResponse> {
    const params = options?.onConflict ? { on_conflict: options.onConflict } : undefined
    return this.bulkWrite('/templates', data, 'POST', params)
  }

  async updateTemplate(id: string, data: UpdateTemplateRequest): Promise<BulkResultItem> {
    return this.bulkWriteOne('/templates', { ...data, template_id: id }, 'PUT')
  }

  async deleteTemplate(id: string, options?: {
    updatedBy?: string
    version?: number
    force?: boolean
    hardDelete?: boolean
  }): Promise<BulkResultItem> {
    return this.bulkWriteOne('/templates', {
      id,
      version: options?.version,
      force: options?.force,
      hard_delete: options?.hardDelete,
      updated_by: options?.updatedBy,
    }, 'DELETE')
  }

  async validateTemplate(id: string, request: ValidateTemplateRequest = {}): Promise<ValidateTemplateResponse> {
    return this.post(`/templates/${seg(id)}/validate`, request)
  }

  // ---- Inheritance ----

  async getChildren(id: string): Promise<TemplateListResponse> {
    return this.get(`/templates/${seg(id)}/children`)
  }

  async getDescendants(id: string): Promise<TemplateListResponse> {
    return this.get(`/templates/${seg(id)}/descendants`)
  }

  // ---- Draft Mode ----

  async activateTemplate(
    id: string,
    options: { namespace: string; dry_run?: boolean },
  ): Promise<ActivateTemplateResponse> {
    return this.post(`/templates/${seg(id)}/activate`, null, options)
  }

  // ---- Reactivate ----

  /**
   * Reactivate a soft-deleted (inactive) template version (CASE-498).
   *
   * The inverse of soft-delete-by-version (`deleteTemplate(id, { version })`):
   * restores a specific frozen version to active so documents pinned to it
   * can be updated again. Distinct from `activateTemplate`, which is draft-only
   * and addresses the latest version — `version` is required here and targets a
   * known frozen version (there is no "latest" default). Idempotent on an
   * already-active version; a draft version is rejected by the backend.
   */
  async reactivateTemplate(
    id: string,
    version: number,
    options: { namespace: string },
  ): Promise<Template> {
    return this.post(`/templates/${seg(id)}/reactivate`, null, {
      namespace: options.namespace,
      version,
    })
  }

  // ---- Cascade ----

  async cascadeTemplate(id: string): Promise<CascadeResponse> {
    return this.post(`/templates/${seg(id)}/cascade`)
  }

  // ---- Edge-type endpoints ----

  /**
   * Additively widen an edge type's allowed endpoint set (CASE-515).
   *
   * Adds source and/or target endpoint templates to an existing relationship
   * template (PoNIF #7) in place, preserving every existing edge — the
   * supported alternative to the delete+recreate that would strand them.
   * Endpoints are append-only: this only ADDS (removal stays unsupported).
   * Each new endpoint must be a real template; idempotent on already-allowed
   * endpoints. No reindex / reporting migration — the relationship indexes and
   * reporting columns are generic.
   */
  async addEdgeTypeEndpoints(
    id: string,
    options: {
      namespace: string
      addSourceTemplates?: EndpointRefInput[]
      addTargetTemplates?: EndpointRefInput[]
    },
  ): Promise<Template> {
    return this.post(
      `/templates/${seg(id)}/endpoints`,
      {
        add_source_templates: options.addSourceTemplates ?? [],
        add_target_templates: options.addTargetTemplates ?? [],
      },
      { namespace: options.namespace },
    )
  }
}
