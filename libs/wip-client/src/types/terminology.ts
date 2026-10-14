import type { BulkResultItem, PaginatedResponse } from './common.js'

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
  /** Reject (not just warn about) term writes whose names collide with
   * another term's value or name. */
  unique_names: boolean
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
  unique_names?: boolean
  metadata?: Partial<TerminologyMetadata>
  created_by?: string
}

export interface UpdateTerminologyRequest {
  value?: string
  label?: string
  /** Explicit null clears; omitted means unchanged. */
  description?: string | null
  case_sensitive?: boolean
  allow_multiple?: boolean
  extensible?: boolean
  mutable?: boolean
  unique_names?: boolean
  metadata?: Partial<TerminologyMetadata>
  updated_by?: string
}

export type TerminologyListResponse = PaginatedResponse<Terminology>

/** Legacy per-language label shape, accepted at the API edge and served
 * as a derived projection; stored terms carry TermName entries instead. */
export interface TermTranslation {
  language: string
  label: string
  description?: string
}

/** One name of a term — the unified naming model. Every name resolves in
 * validation exactly like the term's value. */
export interface TermName {
  value: string
  /** Name type from the extensible _NAME_TYPES terminology
   * (e.g. 'INN', 'TRADE_GLOBAL', 'CODE'); null/absent = untyped synonym. */
  type?: string | null
  /** ISO 639-1 language code for language-specific names. */
  language?: string | null
  description?: string | null
  /** Where this name came from (e.g. 'RTiS', 'OBO:hasExactSynonym'). */
  source?: string | null
  /** At most one preferred name per (type, language) group. */
  preferred?: boolean
}

export interface Term {
  term_id: string
  namespace: string
  terminology_id: string
  terminology_value?: string
  value: string
  names: TermName[]
  /** Derived projection: every name's value, flat. */
  aliases: string[]
  label?: string
  description?: string
  sort_order: number
  parent_term_id?: string
  /** Derived projection: language-bearing, untyped names. */
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
  names?: TermName[]
  /** Legacy shape: plain strings become untyped names. */
  aliases?: string[]
  label?: string
  description?: string
  sort_order?: number
  parent_term_id?: string
  /** Legacy shape: entries become language-bearing names. */
  translations?: TermTranslation[]
  metadata?: Record<string, unknown>
  created_by?: string
}

export interface UpdateTermRequest {
  value?: string
  /** Full replacement of the names list; mutually exclusive with the
   * legacy aliases/translations fields. Pass [] to remove all names
   * (null is rejected). */
  names?: TermName[]
  /** Legacy update: replaces only the untyped, language-less names. */
  aliases?: string[]
  /** Explicit null clears (display falls back to the value); omitted
   * means unchanged — RFC 7396 semantics, like document PATCH. */
  label?: string | null
  /** Explicit null clears; omitted means unchanged. */
  description?: string | null
  /** Explicit null resets to 0; omitted means unchanged. */
  sort_order?: number | null
  /** Explicit null detaches from the parent; omitted means unchanged. */
  parent_term_id?: string | null
  /** Legacy update: replaces only the language-bearing, untyped names. */
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
  // The wire key is `relations` (matches def-store import service:
  // `data.get("relations", [])` in import_export.py). The previous
  // `relationships?` key was a latent client-side bug — payloads sent
  // under that key were silently dropped on the backend.
  relations?: Array<{
    source_term_value: string
    target_term_value: string
    relation_type: string
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
  // Wire fields from def-store's _export_json (import_export.py): the format
  // tag is `format`, not `export_format`; `version` is the export schema
  // version; `relations` is present only when the export pulled any term
  // relations (includeRelations).
  format: string
  version: string
  relations?: Array<{
    source_term_value: string
    target_term_value: string
    relation_type: string
    metadata?: Record<string, unknown>
    source_terminology_id?: string
    target_terminology_id?: string
  }>
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
  /** The exact name that matched (set when matched_via is 'alias'),
   * with its type and language. */
  matched_name?: TermName
  /** How many distinct terms bear the submitted value; more than 1 means
   * the name is ambiguous and matched_term is merely the first bearer in
   * storage order. */
  matches?: number
  /** Every bearer of the name, populated when matches > 1. */
  candidates?: TermMatchCandidate[]
  suggestion?: Term
  error?: string
}

/** One bearer of an ambiguous name. */
export interface TermMatchCandidate {
  term: Term
  matched_name: TermName
}

/** One ranked hit from the term search (typeahead) surface. */
export interface TermSearchHit {
  term: Term
  /** The exact string that matched. */
  matched_value: string
  matched_via: 'value' | 'label' | 'alias'
  /** The name that matched, with type and language (alias hits only). */
  matched_name?: TermName | null
  rank: number
  /** rollup 'root' only: the term that actually matched, when `term` was
   * replaced by its root ancestor. */
  matched_term?: Term | null
}

export interface TermSearchResponse {
  query: string
  mode: string
  rollup: string
  items: TermSearchHit[]
  total_matched: number
}

/** Policy + desired term set for a terminology snapshot reconcile. */
export interface ReconcileTerminologyRequest {
  /** The complete desired term set, keyed by value. */
  terms: CreateTermRequest[]
  /** 'ignore' (default) counts absent active terms; 'deprecate' retires them. */
  on_missing?: 'ignore' | 'deprecate'
  /** Absent value -> successor value (may itself be new in the snapshot). */
  successors?: Record<string, string>
  deprecation_reason?: string
  /** true (default): return the plan, write nothing. */
  dry_run?: boolean
}

export interface ReconcileCounts {
  created: number
  updated: number
  unchanged: number
  deprecated: number
  missing_ignored: number
  errors: number
}

export interface ReconcileTerminologyResponse {
  dry_run: boolean
  terminology_id: string
  terminology_value: string
  counts: ReconcileCounts
  /** Rows for creates, updates, deprecations and errors — unchanged terms
   * are counted, not listed. */
  results: BulkResultItem[]
  total_submitted: number
  total_existing_active: number
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

// ---- Audit Log ----

export interface AuditLogEntry {
  term_id: string
  terminology_id: string
  action: 'created' | 'updated' | 'deprecated' | 'deleted'
  changed_at: string
  changed_by: string | null
  changed_fields: string[]
  previous_values: Record<string, unknown>
  new_values: Record<string, unknown>
  comment: string | null
}

export interface AuditLogResponse {
  items: AuditLogEntry[]
  total: number
  page: number
  page_size: number
}
