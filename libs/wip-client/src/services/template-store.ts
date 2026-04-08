import { BaseService } from './base.js'
import type { BulkResponse, BulkResultItem } from '../types/common.js'
import type {
  Template,
  TemplateListResponse,
  CreateTemplateRequest,
  UpdateTemplateRequest,
  ValidateTemplateRequest,
  ValidateTemplateResponse,
  TemplateUpdateResponse,
  ActivateTemplateResponse,
  CascadeResponse,
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
  }): Promise<TemplateListResponse> {
    return this.get('/templates', params)
  }

  async getTemplate(id: string, version?: number): Promise<Template> {
    return this.get(`/templates/${id}`, version ? { version } : undefined)
  }

  async getTemplateRaw(id: string, version?: number): Promise<Template> {
    return this.get(`/templates/${id}/raw`, version ? { version } : undefined)
  }

  async getTemplateByValue(value: string): Promise<Template> {
    return this.get(`/templates/by-value/${value}`)
  }

  async getTemplateByValueRaw(value: string, namespace: string): Promise<Template> {
    return this.get(`/templates/by-value/${value}/raw?namespace=${encodeURIComponent(namespace)}`)
  }

  async getTemplateVersions(value: string): Promise<TemplateListResponse> {
    return this.get(`/templates/by-value/${value}/versions`)
  }

  async getTemplateByValueAndVersion(value: string, version: number): Promise<Template> {
    return this.get(`/templates/by-value/${value}/versions/${version}`)
  }

  /**
   * Create a single template.
   *
   * @param data - The template definition.
   * @param options - Optional behavior flags.
   * @param options.onConflict - How to handle a value collision in the same
   *   namespace. `'error'` (default) treats it as an error. `'validate'` makes
   *   the call idempotent for app bootstrap: identical schema returns
   *   `status='unchanged'`; compatible (added optional fields only) bumps to
   *   version N+1; incompatible throws `WipBulkItemError` with
   *   `errorCode='incompatible_schema'` and a structured `details` diff.
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
    return this.post(`/templates/${id}/validate`, request)
  }

  // ---- Inheritance ----

  async getChildren(id: string): Promise<TemplateListResponse> {
    return this.get(`/templates/${id}/children`)
  }

  async getDescendants(id: string): Promise<TemplateListResponse> {
    return this.get(`/templates/${id}/descendants`)
  }

  // ---- Draft Mode ----

  async activateTemplate(
    id: string,
    options: { namespace: string; dry_run?: boolean },
  ): Promise<ActivateTemplateResponse> {
    return this.post(`/templates/${id}/activate`, null, options)
  }

  // ---- Cascade ----

  async cascadeTemplate(id: string): Promise<CascadeResponse> {
    return this.post(`/templates/${id}/cascade`)
  }
}
