export interface IntegrityIssue {
  type: string
  severity: string
  source: string
  entity_id: string
  entity_value: string | null
  field_path: string | null
  reference: string
  message: string
}

export interface IntegritySummary {
  total_templates: number
  total_documents: number
  documents_checked: number
  templates_with_issues: number
  documents_with_issues: number
  orphaned_terminology_refs: number
  orphaned_template_refs: number
  orphaned_term_refs: number
  inactive_refs: number
}

export interface IntegrityCheckResult {
  status: 'healthy' | 'warning' | 'error' | 'partial'
  checked_at: string
  services_checked: string[]
  services_unavailable: string[]
  summary: IntegritySummary
  issues: IntegrityIssue[]
}

export interface SearchResult {
  type: 'terminology' | 'term' | 'template' | 'document' | 'file'
  id: string
  value: string | null
  label: string | null
  status: string | null
  description: string | null
  updated_at: string | null
}

export interface SearchResponse {
  query: string
  results: SearchResult[]
  counts: Record<string, number>
  total: number
}

export interface ActivityItem {
  type: 'terminology' | 'term' | 'template' | 'document' | 'file'
  action: 'created' | 'updated' | 'deleted' | 'deprecated'
  entity_id: string
  entity_value: string | null
  entity_label: string | null
  timestamp: string
  user: string | null
  version: number | null
  details: Record<string, unknown> | null
}

export interface ActivityResponse {
  activities: ActivityItem[]
  total: number
}

export interface DocumentReference {
  document_id: string
  template_id: string
  template_value: string | null
  field_path: string
  status: string
  created_at: string | null
}

export interface TermDocumentsResponse {
  term_id: string
  documents: DocumentReference[]
  total: number
}

export interface EntityReference {
  ref_type: 'template' | 'terminology' | 'term'
  ref_id: string
  ref_value: string | null
  ref_label: string | null
  field_path: string | null
  status: 'valid' | 'broken' | 'inactive'
  error: string | null
}

export interface EntityDetails {
  entity_type: 'document' | 'template' | 'terminology' | 'term' | 'file'
  entity_id: string
  entity_value: string | null
  entity_label: string | null
  entity_status: string | null
  version: number | null
  created_at: string | null
  updated_at: string | null
  data: Record<string, unknown> | null
  references: EntityReference[]
  valid_refs: number
  broken_refs: number
  inactive_refs: number
}

export interface EntityReferencesResponse {
  entity: EntityDetails | null
  error: string | null
}

export interface IncomingReference {
  entity_type: 'document' | 'template'
  entity_id: string
  entity_value: string | null
  entity_label: string | null
  entity_status: string | null
  field_path: string | null
  reference_type: 'uses_template' | 'extends' | 'template_ref' | 'terminology_ref' | 'term_ref' | 'file_ref'
}

export interface ReferencedByResponse {
  entity_type: 'document' | 'template' | 'terminology' | 'term' | 'file'
  entity_id: string
  entity_value: string | null
  entity_label: string | null
  referenced_by: IncomingReference[]
  total: number
  error: string | null
}
