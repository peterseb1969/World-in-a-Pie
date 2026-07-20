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
  /** Pinned version of template_ref. Backend (CASE-493) requires this when template_ref is set. */
  template_ref_version?: number
  reference_type?: ReferenceType
  target_templates?: string[]
  target_terminologies?: string[]
  version_strategy?: VersionStrategy
  file_config?: FileFieldConfig
  array_item_type?: FieldType
  array_terminology_ref?: string
  array_template_ref?: string
  /** Pinned version of array_template_ref. Backend (CASE-493) requires this when array_template_ref is set. */
  array_template_ref_version?: number
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

/**
 * Opt-in cross-version entity view over a template's per-version reporting
 * tables — the config behind the bare `doc_<value>` name.
 *
 * The identity core (the typed intersection of the selected versions'
 * tables) is always included; `columns` adds mappings beyond it. Anything
 * unmapped and not provably identical across the selected versions is
 * absent from the view — the backend never silently merges columns it
 * cannot prove compatible.
 */
export interface CrossVersionView {
  /** Version tables the view spans. Server default is 'all'. */
  versions: 'all' | number[]
  /**
   * Target column → optional source. `{ from: old }` maps a renamed column
   * (declared renames land here). `{}` or `null` means the column keeps its
   * own name in the versions that have it, and is NULL elsewhere.
   */
  columns: Record<string, { from?: string } | null>
}

export interface ReportingConfig {
  sync_enabled: boolean
  sync_strategy: SyncStrategy
  table_name?: string
  include_metadata: boolean
  flatten_arrays: boolean
  max_array_elements: number
  /**
   * Opt-in cross-version view config. Stored pass-through on the template;
   * the shape is owned and validated by reporting-sync, which builds the
   * view. Absent / null means the bare name exposes the identity core only.
   */
  cross_version_view?: CrossVersionView | null
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
   * Fields to surface in peer/header projection contexts (CASE-343).
   * Bare names target `data.<name>`; `metadata.custom.<name>` paths
   * are allowed for audit fields. Empty → the platform's projection
   * falls back to `identity_fields`. The relationships endpoint's
   * `?include=peers` projection reads this; future list / summary
   * endpoints may consume it too.
   */
  header_fields?: string[]
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
  /**
   * Field renames this version declared relative to the previous one, as
   * `{new_field: old_field}`. Persisted, so it comes back on read — a
   * template editor renders the declaration it was created with.
   */
  renames?: Record<string, string> | null
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
  /**
   * Peer/header-projection fields (CASE-343). Bare names target data.*,
   * `metadata.custom.<name>` paths allowed. Empty → projection falls
   * back to identity_fields.
   */
  header_fields?: string[]
  /** Usage class — defaults to 'entity' on the server when omitted. */
  usage?: TemplateUsage
  /** Required when usage='relationship'; ignored otherwise. */
  source_templates?: string[]
  /** Required when usage='relationship'; ignored otherwise. */
  target_templates?: string[]
  /** Defaults to true. Immutable after creation. See PoNIF #8. */
  versioned?: boolean
  /**
   * Field renames relative to the previous version, `{new_field: old_field}`.
   * Validated against the version being renamed from (the old name existed
   * and is gone, the new one is new, types match, identity fields excluded)
   * and rejected on a first version. A declared rename migrates losslessly
   * and maps in reporting; an undeclared one strands the old column's data.
   */
  renames?: Record<string, string>
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
  /** Update peer/header-projection fields (CASE-343). */
  header_fields?: string[]
  /**
   * Field renames the new version declares relative to the current one,
   * `{new_field: old_field}`. This is the path an interactive editor takes —
   * renaming a field on an existing template — so it matters here as much as
   * on create. Same validation as the create path.
   */
  renames?: Record<string, string>
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

/**
 * Live-document impact of a version event, computed advisory-side from
 * document-store.
 *
 * `status: 'unavailable'` is a real answer, not an error: document-store
 * could not be reached, so the counts are unknown. It is deliberately never
 * a silent zero, which would read as "no documents affected".
 */
export interface TemplateVersionImpact {
  status: 'ok' | 'unavailable'
  /** Present when status === 'ok'. */
  total_live_docs?: number
  /** Live document count keyed by the template version they validated against. */
  docs_per_version?: Record<string, number>
  /** Per-field count of live documents where the field is non-empty. */
  field_nonempty_counts?: Record<string, number>
  /** Present when status === 'unavailable' — why the counts are missing. */
  reason?: string
}

/**
 * Whether the platform can offer to move existing documents onto the new
 * version, and how.
 */
export interface TemplateMigrationOffer {
  /**
   * true = offerable; false = needs app-side data decisions (type changes,
   * newly-required fields, removed fields carrying live data); null =
   * removed fields present but the counts were unavailable, so run the
   * dry-run for a per-document readiness report.
   */
  eligible: boolean | null
  reason: string
  /** The operation to run — the migrate dry-run/apply cycle. */
  via: string
}

/**
 * `details` on a bulk result item that minted a new template version: the
 * schema diff, plus the consequences of having minted it.
 *
 * Both write paths carry this — create-as-upsert (`POST /templates`) and
 * update (`PUT /templates`). Older backends attached it on create only, so
 * treat it as optional and narrow with `asVersionEventDetails`.
 */
export interface TemplateVersionEventDetails {
  added_optional: string[]
  added_required: string[]
  removed: string[]
  changed_type: Array<{ name: string; old_type: string; new_type: string }>
  made_required: string[]
  modified_existing: string[]
  identity_changed: { old: string[]; new: string[] } | null
  relationship_refs_changed: unknown | null
  impact: TemplateVersionImpact
  migration: TemplateMigrationOffer
}

/**
 * Narrow a bulk result item's untyped `details` to the version-event shape.
 *
 * `BulkResultItem.details` is `Record<string, unknown>` because the envelope
 * is shared by every bulk endpoint — documents, terms, templates. Rather
 * than widening it with template-specific members, narrow here at the point
 * of use.
 *
 * Returns null when the item did not mint a version, or when the backend
 * predates the impact block on the path that was used.
 */
export function asVersionEventDetails(
  details: Record<string, unknown> | undefined | null,
): TemplateVersionEventDetails | null {
  if (!details) return null
  const impact = details.impact as TemplateVersionImpact | undefined
  const migration = details.migration as TemplateMigrationOffer | undefined
  if (!impact || !migration) return null
  return details as unknown as TemplateVersionEventDetails
}
