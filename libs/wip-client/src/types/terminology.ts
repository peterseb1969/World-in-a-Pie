import type { PaginatedResponse } from './common.js'

export interface TerminologyMetadata {
  source?: string
  source_url?: string
  version?: string
  language: string
  custom: Record<string, unknown>
}

export interface Terminology {
  terminology_id: string
  namespace: string
  value: string
  label: string
  description?: string
  case_sensitive: boolean
  allow_multiple: boolean
  extensible: boolean
  mutable: boolean
  metadata: TerminologyMetadata
  status: 'active' | 'inactive'
  term_count: number
  created_at: string
  created_by?: string
  updated_at: string
  updated_by?: string
}

export interface CreateTerminologyRequest {
  value: string
  label: string
  description?: string
  namespace: string
  case_sensitive?: boolean
  allow_multiple?: boolean
  extensible?: boolean
  mutable?: boolean
  metadata?: Partial<TerminologyMetadata>
  created_by?: string
}

export interface UpdateTerminologyRequest {
  value?: string
  label?: string
  description?: string
  case_sensitive?: boolean
  allow_multiple?: boolean
  extensible?: boolean
  mutable?: boolean
  metadata?: Partial<TerminologyMetadata>
  updated_by?: string
}

export type TerminologyListResponse = PaginatedResponse<Terminology>

export interface TermTranslation {
  language: string
  label: string
  description?: string
}

export interface Term {
  term_id: string
  namespace: string
  terminology_id: string
  terminology_value?: string
  value: string
  aliases: string[]
  label?: string
  description?: string
  sort_order: number
  parent_term_id?: string
  translations: TermTranslation[]
  metadata: Record<string, unknown>
  status: 'active' | 'deprecated' | 'inactive'
  deprecated_reason?: string
  replaced_by_term_id?: string
  created_at: string
  created_by?: string
  updated_at: string
  updated_by?: string
}

export interface CreateTermRequest {
  value: string
  aliases?: string[]
  label?: string
  description?: string
  sort_order?: number
  parent_term_id?: string
  translations?: TermTranslation[]
  metadata?: Record<string, unknown>
  created_by?: string
}

export interface UpdateTermRequest {
  value?: string
  aliases?: string[]
  label?: string
  description?: string
  sort_order?: number
  parent_term_id?: string
  translations?: TermTranslation[]
  metadata?: Record<string, unknown>
  updated_by?: string
}

export interface DeprecateTermRequest {
  reason: string
  replaced_by_term_id?: string
  updated_by?: string
}

export interface TermListResponse extends PaginatedResponse<Term> {
  terminology_id: string
  terminology_value: string
}

export interface ImportTerminologyRequest {
  terminology: CreateTerminologyRequest
  terms: CreateTermRequest[]
  relationships?: Array<{
    source_term_value: string
    target_term_value: string
    relationship_type: string
    target_terminology_value?: string
  }>
  options?: {
    skip_duplicates?: boolean
    update_existing?: boolean
  }
}

export interface ExportTerminologyResponse {
  terminology: Terminology
  terms: Term[]
  export_date: string
  export_format: string
}

export interface ValidateValueRequest {
  terminology_id?: string
  terminology_value?: string
  value: string
}

export interface ValidateValueResponse {
  valid: boolean
  terminology_id: string
  terminology_value: string
  value: string
  matched_term?: Term
  matched_via?: 'value' | 'alias'
  suggestion?: Term
  error?: string
}

export interface BulkValidateRequest {
  items: ValidateValueRequest[]
}

export interface BulkValidateResponse {
  results: ValidateValueResponse[]
  total: number
  valid_count: number
  invalid_count: number
}
