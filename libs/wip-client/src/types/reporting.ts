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
  /**
   * Namespace whose PostgreSQL schema unqualified table names resolve in.
   * Each namespace is its own schema (a table is `"<ns>"."doc_<value>"`);
   * when set, the server runs the query with search_path pointed there, so
   * `doc_<value>` works unqualified. Omit for cross-namespace queries and
   * schema-qualify each table in the SQL instead.
   */
  namespace?: string
}

export interface ReportQueryResult {
  /**
   * Result column names, from the statement's own result description —
   * populated even when `rows` is empty.
   */
  columns: string[]
  /**
   * Each row is an object keyed by column name — the server returns
   * records, not positional arrays. (This type once said `unknown[][]`,
   * which compiled against code that then read `undefined` at runtime.)
   */
  rows: Array<Record<string, unknown>>
  row_count: number
  truncated: boolean
}

// ── Table/Schema types ──

export interface ReportTableColumn {
  name: string
  type: string
  nullable: boolean
}

/**
 * One reporting relation as `/tables` lists it. Post per-version split,
 * a template ("entity") owns several relations: physical per-version
 * tables `doc_<value>__v<N>`, the identity-core view
 * `doc_<value>__entities`, and the bare-name view `doc_<value>` — the
 * default query surface. Never sum an entity's sibling relations (they
 * overlap by construction); use `ReportEntity.row_count` instead.
 */
export interface ReportTable {
  namespace: string
  name: string
  /** 'view' (entity views) or 'table' (physical / legacy pre-split). */
  kind: 'view' | 'table'
  /** The entity (doc_* stem, version/__entities suffix removed) for document relations, null for metadata tables. */
  template_value: string | null
  qualified_name: string
  /**
   * A table's rows; an entity view's is its version tables' summed. Null
   * when not counted (`row_count_note` says why: another kind of view, or a
   * count that took too long).
   */
  row_count: number | null
  row_count_note?: string
  /** Summary mode only. */
  column_count?: number
  /** Detail mode (listTables with a tableName) only. */
  columns?: ReportTableColumn[]
}

export interface ReportEntityVersion {
  version: number
  table: string
  /** Null when the count took too long. */
  row_count: number | null
}

/**
 * Entity-first grouping from `/tables` — one entry per template with its
 * version tables and views. `row_count` is the entity's document count
 * (a document lives in exactly one version table under latest_only).
 * `legacy_table: true` = a pre-split physical table still occupies the
 * bare name (needs an explicit drop + batch-sync rebuild — the platform
 * never auto-drops it).
 */
export interface ReportEntity {
  namespace: string
  entity: string
  default_view: string
  default_view_present: boolean
  entities_view: string
  legacy_table: boolean
  versions: ReportEntityVersion[]
  /** Null when a version table could not be counted. */
  row_count: number | null
}

export interface ReportTableSchema {
  namespace: string
  template_value: string
  schema: string
  table_name: string
  qualified_name: string
  columns: ReportTableColumn[]
  /** Null when not counted; `row_count_note` says why. */
  row_count: number | null
  row_count_note: string | null
}

// ── Sync Status types ──

/** An event the sync worker gave up on after its last delivery attempt. */
export interface DroppedEvent {
  event_type: string
  entity_id: string | null
  namespace: string | null
  error: string
  /** Delivery attempts made before the event was dropped. */
  deliveries: number
  dropped_at: string
}

export interface SyncStatus {
  running: boolean
  connected_to_nats: boolean
  connected_to_postgres: boolean
  /** True once the service runs on its own unprivileged PostgreSQL roles; null before the first connection. */
  postgres_service_roles_ok: boolean | null
  last_event_processed: string | null
  events_processed: number
  /** Every failed attempt, retries that later succeed included. */
  events_failed: number
  /** Terminal losses only: events that exhausted their retries and will never be applied. */
  events_dropped: number
  recent_drops: DroppedEvent[]
  /** Events for a namespace deleted while they were in flight; expected after a deletion. */
  events_skipped_deleted_namespace: number
  tables_managed: number
}

// ── Health ──

export interface HealthResponse {
  status: 'healthy' | 'degraded' | 'unhealthy'
  service: string
  version: string
  nats_connected: boolean
  postgres_connected: boolean
  details: Record<string, unknown>
}

// ── Metrics ��─

export interface PerTemplateStats {
  template_value: string
  /** Namespace whose documents these are (null: not known). */
  namespace: string | null
  /** Table the latest event wrote (null: no event named one). */
  table_name: string | null
  documents_synced: number
  documents_failed: number
  last_sync_at: string | null
  last_error: string | null
  last_error_at: string | null
}

export interface ConsumerInfo {
  stream_name: string
  consumer_name: string
  pending_messages: number
  /** Not measured (JetStream reports no byte count): always null. */
  pending_bytes: number | null
  /** Messages this consumer has delivered, redeliveries included. */
  delivered_messages: number
  ack_pending: number
  redelivered: number
  last_delivered: string | null
}

export interface LatencyStats {
  sample_count: number
  min_ms: number
  max_ms: number
  avg_ms: number
  p50_ms: number
  p95_ms: number
  p99_ms: number
}

export interface MetricsResponse {
  started_at: string
  uptime_seconds: number
  nats_connected: boolean
  postgres_connected: boolean
  events_processed: number
  events_failed: number
  events_per_second: number
  consumer_info: ConsumerInfo | null
  processing_latency: LatencyStats
  template_stats: PerTemplateStats[]
  errors_by_type: Record<string, number>
}

// ── Alerts ──

export type AlertSeverity = 'info' | 'warning' | 'critical'
export type AlertType = 'queue_lag' | 'error_rate' | 'processing_stalled' | 'connection_lost'

export interface Alert {
  alert_id: string
  alert_type: AlertType
  severity: AlertSeverity
  message: string
  triggered_at: string
  resolved_at: string | null
  details: Record<string, unknown>
}

export interface AlertThresholds {
  queue_lag_warning: number
  queue_lag_critical: number
  /** % of the events since the previous check that failed. */
  error_rate_warning: number
  /** % of the events since the previous check that failed. */
  error_rate_critical: number
  stall_warning_seconds: number
  stall_critical_seconds: number
}

export interface AlertConfig {
  enabled: boolean
  check_interval_seconds: number
  thresholds: AlertThresholds
  webhook_url: string | null
  webhook_headers: Record<string, string>
}

export interface AlertsResponse {
  config: AlertConfig
  active_alerts: Alert[]
  resolved_alerts: Alert[]
}

// ── Batch Sync ──

export type BatchSyncStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'

export interface BatchSyncRequest {
  template_value?: string
  force?: boolean
  page_size?: number
}

export interface BatchSyncJob {
  job_id: string
  template_value: string
  /** Resolved canonical template id — a value alone is ambiguous when
   * several namespaces share it. */
  template_id?: string | null
  /** Document scope: null = all namespaces, set = only that namespace. */
  namespace?: string | null
  status: BatchSyncStatus
  started_at: string | null
  completed_at: string | null
  total_documents: number
  documents_synced: number
  documents_failed: number
  current_page: number
  error_message: string | null
  /** Where the job's time went, in milliseconds. */
  fetch_ms: number
  upsert_ms: number
  sibling_ms: number
  /** Physical row upserts (one per document version written). */
  rows_written: number
  /** True when a duplicate trigger joined this running job instead of starting one (a force was then not applied). */
  deduplicated: boolean
  /** Relations a force rebuild removed before this job synced (schema-qualified). */
  dropped_relations: string[]
}

export interface BatchSyncResponse {
  job_id: string
  template_value: string
  /** Document scope the job was started with (null = all namespaces). */
  namespace?: string | null
  status: BatchSyncStatus
  message: string
}

/**
 * Result shape for the entity-table batch syncs (terminologies, terms,
 * term_relations). Synchronous on the server (no per-job polling); the
 * full result is in the response body.
 */
export interface BatchEntitySyncResult {
  /**
   * `completed`: everything listed and written. `completed_with_errors`:
   * some entries failed (`failed`). `incomplete`: the source listing was cut
   * short, so the counts cover only what was read (`complete: false`).
   */
  status: 'completed' | 'completed_with_errors' | 'incomplete'
  table: 'terminologies' | 'terms' | 'term_relations' | 'templates'
  /** False when the source listing could not be read to the end. */
  complete?: boolean
  /** Entries fetched from the source service. */
  fetched?: number
  /** Entries upserted into PostgreSQL. */
  synced?: number
  /** Entries that failed to upsert. */
  failed?: number
  /** Free-form additional fields the server may emit. */
  [extra: string]: unknown
}

export interface BatchJobCancelResult {
  status: 'cancelled' | 'not_running'
  job_id: string
}

export interface BatchJobsCleared {
  cleared: number
}

// ── CSV Export ──

export interface CsvExportQuery {
  sql: string
  params?: unknown[]
  timeout_seconds?: number
  filename?: string
}

// ── Integrity & References ──

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
  /** `incomplete`: nothing found, but some service did not check everything. */
  status: 'healthy' | 'warning' | 'error' | 'partial' | 'incomplete'
  checked_at: string
  services_checked: string[]
  services_unavailable: string[]
  /** Services that answered but could not run every check (a service they ask was down). */
  services_incomplete: string[]
  /** Services whose scan the request's limit cut short (the window was checked). Absent from older servers. */
  services_truncated?: string[]
  summary: IntegritySummary
  issues: IntegrityIssue[]
}

/**
 * One sync-enabled template's parity row. `table_present` and
 * `missing_columns` refer to the LATEST version's physical table;
 * `actual_rows` sums every version table. Count fields are null when
 * counts were not requested (`includeCounts: false`) or not comparable.
 */
export interface TemplateParity {
  template_value: string
  /**
   * The template's own namespace when another namespace owns it (a shared
   * template this namespace's documents use); null for its own templates.
   */
  template_namespace: string | null
  sync_enabled: boolean
  table_present: boolean
  missing_columns: string[]
  bookkeeping_row: boolean
  counts_comparable: boolean
  expected_documents: number | null
  actual_rows: number | null
  counts_match: boolean | null
  error: string | null
  version_tables: number[]
  view_present: boolean
  legacy_table: boolean
}

/**
 * Parity of one fixed-schema table reporting derives from def-store.
 * `issue` is a stable code when the table does not hold what it should:
 * `terms_mismatch`, `term_relations_mismatch`, `term_names_missing`,
 * `term_closure_stale`, or `check_failed` (the check itself could not run;
 * `error` says why).
 */
export interface DerivedTableParity {
  table: string
  expected_rows: number | null
  actual_rows: number | null
  issue: string | null
  error: string | null
}

/** A view registered for the namespace, and whether it exists as one. */
export interface ViewParity {
  view_name: string
  present: boolean
}

/** Does PostgreSQL reflect what sync should have built for a namespace? */
export interface NamespaceParityResult {
  namespace: string
  schema_name: string
  schema_present: boolean
  table_count: number
  bookkeeping_tables_ok: boolean
  bookkeeping_error: string | null
  templates: TemplateParity[]
  templates_skipped_sync_disabled: number
  structural_issues: number
  count_mismatches: number
  derived_tables: DerivedTableParity[]
  views: ViewParity[]
  /** Failing derived tables plus missing registered views; kept apart from
   * structural_issues, which a restore's structure gate reads mid-restore. */
  derived_issues: number
  ok: boolean
}

export interface SearchResult {
  type: 'terminology' | 'term' | 'template' | 'document' | 'file'
  id: string
  value: string | null
  label: string | null
  status: string | null
  description: string | null
  updated_at: string | null
  /** ts_rank score; populated for FTS document hits only. */
  score?: number | null
  /**
   * ts_headline excerpt; populated for FTS document hits only.
   * HTML by default with <b>...</b> around matched terms; pass
   * snippet_format='text' on the request for plain text.
   */
  snippet?: string | null
}

/**
 * Per-type paginated bucket on `SearchResponse.results` (CASE-329).
 * Mirrors the platform-wide pagination envelope (see `wip://conventions`).
 */
export interface SearchTypeResults {
  items: SearchResult[]
  total: number
  page: number
  page_size: number
  pages: number
  /**
   * False when this type's search could not look everywhere: a source failed
   * (no items) or answered only part of what it holds. An empty, complete
   * bucket means nothing matched.
   */
  complete: boolean
  /** Why the bucket is incomplete; null when complete. */
  error: string | null
}

/**
 * Response from `POST /api/reporting-sync/search`.
 *
 * Breaking change in @wip/client 0.19.0 (CASE-329): the legacy flat
 * `results: SearchResult[]` + `counts: Record<string, number>` shape was
 * replaced by per-type buckets keyed by entity type ('terminology',
 * 'term', 'template', 'document', 'file'), each carrying its own
 * pagination envelope. Consumers iterating "all hits" should iterate
 * the values of `results`.
 */
export interface SearchResponse {
  query: string
  /** Echo of the document-search mode used (informational only). */
  mode?: string | null
  /** Per-type paginated buckets. Only types the search visited appear here. */
  results: Record<string, SearchTypeResults>
  /** Sum of per-type totals across all visited types. */
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
  /** Entity types whose source could not be read: their activity is missing
   * from `activities`. Empty when every requested source answered. */
  incomplete_sources: string[]
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

// ── Registered views (user-defined SQL views in a namespace's schema) ──

/** A registered view as the list endpoint returns it. */
export interface RegisteredView {
  namespace: string
  name: string
  sql: string
  registered_at: string | null
  registered_by: string | null
}

export interface ListViewsResponse {
  namespace: string
  views: RegisteredView[]
}

/** Result of registering (or replacing) a view. */
export interface RegisterViewResult {
  namespace: string
  name: string
  /** Fully qualified, quote-safe relation reference, e.g. `"clintrial"."v_study_search"`. */
  schema_qualified: string
  /** True on first registration (HTTP 201); false on replace (HTTP 200). */
  created: boolean
}

export interface DeleteViewResult {
  namespace: string
  name: string
  dropped: boolean
}

/**
 * One structured predicate for the view rows read. No SQL anywhere: the
 * column must be one of the view's actual columns (a miss is a 400 naming
 * them), the operator comes from this closed set, and the value is bound
 * as a statement parameter server-side.
 */
export interface ViewRowsFilter {
  column: string
  op?:
    | 'eq' | 'ne' | 'lt' | 'lte' | 'gt' | 'gte'
    | 'like' | 'ilike' | 'in' | 'is_null' | 'not_null'
  /** Comparison value; an array for 'in'; omitted for the null checks. */
  value?: unknown
}

export interface ViewRowsSort {
  column: string
  direction?: 'asc' | 'desc'
}

/** Parameters for the structured view read (all optional — an empty body reads the first page). */
export interface ViewRowsParams {
  filters?: ViewRowsFilter[]
  sort?: ViewRowsSort[]
  /** Rows per page (1-1000, default 100). */
  limit?: number
  offset?: number
  /** Query timeout in seconds (1-300, default 30). */
  timeout_seconds?: number
}
