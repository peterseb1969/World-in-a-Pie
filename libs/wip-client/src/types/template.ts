import type { PaginatedResponse } from './common.js'

export type FieldType =
  | 'string'
  | 'number'
  | 'integer'
  | 'boolean'
  | 'date'
  | 'datetime'
  | 'term'
  | 'reference'
  | 'file'
  | 'object'
  | 'array'

export type ReferenceType = 'document' | 'term' | 'terminology' | 'template'
export type VersionStrategy = 'latest' | 'pinned'

export type SemanticType =
  | 'email'
  | 'url'
  | 'latitude'
  | 'longitude'
  | 'percentage'
  | 'duration'
  | 'geo_point'

export interface FieldValidation {
  pattern?: string
  min_length?: number
  max_length?: number
  minimum?: number
  maximum?: number
  enum?: unknown[]
}

export interface FileFieldConfig {
  allowed_types: string[]
  max_size_mb: number
  multiple: boolean
  max_files?: number
}

export interface FieldDefinition {
  name: string
  label: string
  type: FieldType
  mandatory: boolean
  default_value?: unknown
  terminology_ref?: string
  template_ref?: string
  reference_type?: ReferenceType
  target_templates?: string[]
  target_terminologies?: string[]
  version_strategy?: VersionStrategy
  file_config?: FileFieldConfig
  array_item_type?: FieldType
  array_terminology_ref?: string
  array_template_ref?: string
  array_file_config?: FileFieldConfig
  validation?: FieldValidation
  semantic_type?: SemanticType
  metadata: Record<string, unknown>
}

export type RuleType =
  | 'conditional_required'
  | 'conditional_value'
  | 'mutual_exclusion'
  | 'dependency'
  | 'pattern'
  | 'range'

export type ConditionOperator =
  | 'equals'
  | 'not_equals'
  | 'in'
  | 'not_in'
  | 'exists'
  | 'not_exists'

export interface Condition {
  field: string
  operator: ConditionOperator
  value?: unknown
}

export interface ValidationRule {
  type: RuleType
  description?: string
  conditions: Condition[]
  target_field?: string
  target_fields?: string[]
  required?: boolean
  allowed_values?: unknown[]
  pattern?: string
  minimum?: number
  maximum?: number
  error_message?: string
}

export type SyncStrategy = 'latest_only' | 'all_versions'

export interface ReportingConfig {
  sync_enabled: boolean
  sync_strategy: SyncStrategy
  table_name?: string
  include_metadata: boolean
  flatten_arrays: boolean
  max_array_elements: number
}

export interface TemplateMetadata {
  domain?: string
  category?: string
  tags: string[]
  custom: Record<string, unknown>
}

export interface Template {
  template_id: string
  namespace: string
  value: string
  label: string
  description?: string
  version: number
  extends?: string
  extends_version?: number
  identity_fields: string[]
  fields: FieldDefinition[]
  rules: ValidationRule[]
  metadata: TemplateMetadata
  reporting?: ReportingConfig
  status: 'draft' | 'active' | 'inactive'
  created_at: string
  created_by?: string
  updated_at: string
  updated_by?: string
}

export interface CreateTemplateRequest {
  value: string
  label: string
  description?: string
  namespace?: string
  extends?: string
  extends_version?: number
  identity_fields?: string[]
  fields?: FieldDefinition[]
  rules?: ValidationRule[]
  metadata?: Partial<TemplateMetadata>
  reporting?: Partial<ReportingConfig>
  created_by?: string
}

export interface UpdateTemplateRequest {
  value?: string
  label?: string
  description?: string
  extends?: string
  extends_version?: number
  identity_fields?: string[]
  fields?: FieldDefinition[]
  rules?: ValidationRule[]
  metadata?: Partial<TemplateMetadata>
  reporting?: Partial<ReportingConfig>
  updated_by?: string
}

export type TemplateListResponse = PaginatedResponse<Template>

export interface ValidateTemplateRequest {
  check_terminologies?: boolean
  check_templates?: boolean
}

export interface ValidateTemplateResponse {
  valid: boolean
  template_id: string
  errors: Array<{ field: string; code: string; message: string }>
  warnings: Array<{ field: string; code: string; message: string }>
}
