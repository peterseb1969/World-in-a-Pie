// =============================================================================
// FIELD TYPES
// =============================================================================

export type FieldType =
  | 'string'
  | 'number'
  | 'integer'
  | 'boolean'
  | 'date'
  | 'datetime'
  | 'term'
  | 'object'
  | 'array'

export const FIELD_TYPES: { value: FieldType; label: string; description: string }[] = [
  { value: 'string', label: 'String', description: 'Free text value' },
  { value: 'number', label: 'Number', description: 'Decimal number' },
  { value: 'integer', label: 'Integer', description: 'Whole number' },
  { value: 'boolean', label: 'Boolean', description: 'True/false value' },
  { value: 'date', label: 'Date', description: 'Date without time' },
  { value: 'datetime', label: 'DateTime', description: 'Date with time' },
  { value: 'term', label: 'Term (Controlled Vocabulary)', description: 'Value from a terminology - ensures data consistency' },
  { value: 'object', label: 'Object (Nested Template)', description: 'Structured data following another template' },
  { value: 'array', label: 'Array', description: 'List of values' }
]

export interface FieldValidation {
  pattern?: string
  min_length?: number
  max_length?: number
  minimum?: number
  maximum?: number
  enum?: unknown[]
}

export interface FieldDefinition {
  name: string
  label: string
  type: FieldType
  mandatory: boolean
  default_value?: unknown
  terminology_ref?: string
  template_ref?: string
  array_item_type?: FieldType
  array_terminology_ref?: string
  array_template_ref?: string
  validation?: FieldValidation
  metadata: Record<string, unknown>
}

// =============================================================================
// RULE TYPES
// =============================================================================

export type RuleType =
  | 'conditional_required'
  | 'conditional_value'
  | 'mutual_exclusion'
  | 'dependency'
  | 'pattern'
  | 'range'

export const RULE_TYPES: { value: RuleType; label: string; description: string }[] = [
  { value: 'conditional_required', label: 'Conditional Required', description: 'Field required if condition met' },
  { value: 'conditional_value', label: 'Conditional Value', description: 'Field value constrained by condition' },
  { value: 'mutual_exclusion', label: 'Mutual Exclusion', description: 'Only one of listed fields can have value' },
  { value: 'dependency', label: 'Dependency', description: 'Field requires another field' },
  { value: 'pattern', label: 'Pattern', description: 'Regex validation' },
  { value: 'range', label: 'Range', description: 'Numeric range validation' }
]

export type ConditionOperator =
  | 'equals'
  | 'not_equals'
  | 'in'
  | 'not_in'
  | 'exists'
  | 'not_exists'

export const CONDITION_OPERATORS: { value: ConditionOperator; label: string }[] = [
  { value: 'equals', label: 'Equals' },
  { value: 'not_equals', label: 'Not Equals' },
  { value: 'in', label: 'In' },
  { value: 'not_in', label: 'Not In' },
  { value: 'exists', label: 'Exists' },
  { value: 'not_exists', label: 'Not Exists' }
]

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

// =============================================================================
// REPORTING CONFIG
// =============================================================================

export type SyncStrategy = 'latest_only' | 'all_versions'

export const SYNC_STRATEGIES: { value: SyncStrategy; label: string; description: string }[] = [
  { value: 'latest_only', label: 'Latest Only', description: 'Keep only the latest version of each document (upsert)' },
  { value: 'all_versions', label: 'All Versions', description: 'Store all document versions (insert, no updates)' }
]

export interface ReportingConfig {
  sync_enabled: boolean
  sync_strategy: SyncStrategy
  table_name?: string
  include_metadata: boolean
  flatten_arrays: boolean
  max_array_elements: number
}

// =============================================================================
// TEMPLATE TYPES
// =============================================================================

export interface TemplateMetadata {
  domain?: string
  category?: string
  tags: string[]
  custom: Record<string, unknown>
}

export interface Template {
  template_id: string
  code: string
  name: string
  description?: string
  version: number
  extends?: string
  identity_fields: string[]
  fields: FieldDefinition[]
  rules: ValidationRule[]
  metadata: TemplateMetadata
  reporting?: ReportingConfig
  status: 'active' | 'deprecated' | 'inactive'
  created_at: string
  created_by?: string
  updated_at: string
  updated_by?: string
}

export interface CreateTemplateRequest {
  code: string
  name: string
  description?: string
  extends?: string
  identity_fields?: string[]
  fields?: FieldDefinition[]
  rules?: ValidationRule[]
  metadata?: Partial<TemplateMetadata>
  reporting?: Partial<ReportingConfig>
  created_by?: string
}

export interface UpdateTemplateRequest {
  code?: string
  name?: string
  description?: string
  extends?: string
  identity_fields?: string[]
  fields?: FieldDefinition[]
  rules?: ValidationRule[]
  metadata?: Partial<TemplateMetadata>
  reporting?: Partial<ReportingConfig>
  updated_by?: string
}

export interface TemplateListResponse {
  items: Template[]
  total: number
  page: number
  page_size: number
}

// =============================================================================
// BULK OPERATION TYPES
// =============================================================================

export interface BulkCreateTemplateRequest {
  templates: CreateTemplateRequest[]
  created_by?: string
}

export interface TemplateBulkOperationResult {
  index: number
  status: 'created' | 'updated' | 'error' | 'skipped'
  id?: string
  code?: string
  error?: string
}

export interface TemplateBulkOperationResponse {
  results: TemplateBulkOperationResult[]
  total: number
  succeeded: number
  failed: number
}

// =============================================================================
// VALIDATION TYPES
// =============================================================================

export interface ValidationError {
  field: string
  code: string
  message: string
}

export interface ValidationWarning {
  field: string
  code: string
  message: string
}

export interface ValidateTemplateRequest {
  check_terminologies?: boolean
  check_templates?: boolean
}

export interface ValidateTemplateResponse {
  valid: boolean
  template_id: string
  errors: ValidationError[]
  warnings: ValidationWarning[]
}
