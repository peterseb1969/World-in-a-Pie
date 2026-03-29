// ── SQL Query types ──

export interface ReportQueryParams {
  /** SQL SELECT query (write operations forbidden) */
  sql: string
  /** Positional parameters ($1, $2, ...) */
  params?: unknown[]
  /** Query timeout in seconds (1-300, default 30) */
  timeout_seconds?: number
  /** Max rows returned (1-50000, default 1000) */
  max_rows?: number
}

export interface ReportQueryResult {
  columns: string[]
  rows: unknown[][]
  row_count: number
  truncated: boolean
}

// ── Table/Schema types ──

export interface ReportTableColumn {
  name: string
  type: string
  nullable: boolean
}

export interface ReportTable {
  table_name: string
  row_count: number
}

export interface ReportTableSchema {
  template_value: string
  table_name: string
  columns: ReportTableColumn[]
  row_count: number
}

// ── Sync Status types ──

export interface SyncStatus {
  running: boolean
  connected_to_nats: boolean
  connected_to_postgres: boolean
  last_event_processed: string | null
  events_processed: number
  events_failed: number
  tables_managed: number
}

// ── Existing types ──

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
