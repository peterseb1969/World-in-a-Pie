import { BaseService } from './base.js'
import type { BulkResponse, BulkResultItem } from '../types/common.js'
import type {
  Template,
  TemplateListResponse,
  CreateTemplateRequest,
  UpdateTemplateRequest,
  ValidateTemplateRequest,
  ValidateTemplateResponse,
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

  async getTemplateByValueRaw(value: string): Promise<Template> {
    return this.get(`/templates/by-value/${value}/raw`)
  }

  async getTemplateVersions(value: string): Promise<TemplateListResponse> {
    return this.get(`/templates/by-value/${value}/versions`)
  }

  async getTemplateByValueAndVersion(value: string, version: number): Promise<Template> {
    return this.get(`/templates/by-value/${value}/versions/${version}`)
  }

  async createTemplate(data: CreateTemplateRequest): Promise<BulkResultItem> {
    return this.bulkWriteOne('/templates', data)
  }

  async createTemplates(data: CreateTemplateRequest[]): Promise<BulkResponse> {
    return this.bulkWrite('/templates', data)
  }

  async updateTemplate(id: string, data: UpdateTemplateRequest): Promise<BulkResultItem> {
    return this.bulkWriteOne('/templates', { ...data, template_id: id }, 'PUT')
  }

  async deleteTemplate(id: string, options?: {
    updatedBy?: string
    version?: number
    force?: boolean
  }): Promise<BulkResultItem> {
    return this.bulkWriteOne('/templates', {
      id,
      version: options?.version,
      force: options?.force,
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
  ): Promise<{ activated: string[] }> {
    return this.post(`/templates/${id}/activate`, null, options)
  }
}
