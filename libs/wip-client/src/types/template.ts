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
  include_subtypes?: boolean
  full_text_indexed?: boolean
  inherited?: boolean
  inherited_from?: string
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

/**
 * How a template's documents are intended to be used.
 *
 * - `entity` (default): full document lifecycle, the v1.x behaviour.
 * - `reference`: lightweight controlled-vocabulary documents (LOV).
 *   Reserved for a future phase; currently behaves like entity.
 * - `relationship`: typed, property-carrying edge between two
 *   documents (a.k.a. "edge type"). Requires source_templates /
 *   target_templates to be set on the template, plus source_ref /
 *   target_ref reference fields. Immutable after creation.
 *
 * See PoNIF #7 (edge types are stored as templates) and PoNIF #8
 * (`versioned: false` is an option on relationship templates).
 */
export type TemplateUsage = 'entity' | 'reference' | 'relationship'

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
  /**
   * Usage class: entity (default), reference, or relationship.
   * Immutable after creation. Relationship templates ("edge types")
   * additionally require source_templates + target_templates and
   * source_ref / target_ref reference fields.
   */
  usage?: TemplateUsage
  /**
   * Template values allowed as the source endpoint of an edge.
   * Set only on relationship templates; empty / absent on entity and
   * reference templates.
   */
  source_templates?: string[]
  /**
   * Template values allowed as the target endpoint of an edge.
   * Set only on relationship templates.
   */
  target_templates?: string[]
  /**
   * True (default) = updates create new versions; false = overwrite
   * in place. Currently only available on relationship templates.
   * Immutable after creation. See PoNIF #8.
   */
  versioned?: boolean
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
  template_id?: string
  version?: number
  namespace: string
  extends?: string
  extends_version?: number
  identity_fields?: string[]
  /** Usage class — defaults to 'entity' on the server when omitted. */
  usage?: TemplateUsage
  /** Required when usage='relationship'; ignored otherwise. */
  source_templates?: string[]
  /** Required when usage='relationship'; ignored otherwise. */
  target_templates?: string[]
  /** Defaults to true. Immutable after creation. See PoNIF #8. */
  versioned?: boolean
  fields?: FieldDefinition[]
  rules?: ValidationRule[]
  metadata?: Partial<TemplateMetadata>
  reporting?: Partial<ReportingConfig>
  created_by?: string
  validate_references?: boolean
  status?: string
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
  will_also_activate?: string[]
}

export interface TemplateUpdateResponse {
  template_id: string
  value: string
  version: number
  is_new_version: boolean
  previous_version?: number
}

export interface ActivationDetail {
  template_id: string
  value: string
  status: string
}

export interface ActivateTemplateResponse {
  activated: string[]
  activation_details: ActivationDetail[]
  total_activated: number
  errors: Array<{ field: string; code: string; message: string }>
  warnings: Array<{ field: string; code: string; message: string }>
}

export interface CascadeResult {
  value: string
  old_template_id: string
  new_template_id?: string
  new_version?: number
  status: string
  error?: string
}

export interface CascadeResponse {
  parent_template_id: string
  parent_value: string
  parent_version: number
  total: number
  updated: number
  unchanged: number
  failed: number
  results: CascadeResult[]
}
