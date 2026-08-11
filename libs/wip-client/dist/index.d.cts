declare class ApiKeyAuthProvider implements AuthProvider {
    private apiKey;
    readonly cacheable = true;
    constructor(apiKey: string);
    getHeaders(): Record<string, string>;
    setApiKey(key: string): void;
}

/**
 * OIDC auth provider. Consumer provides a callback that returns the current token.
 * No OIDC library dependency — the consumer handles token acquisition/refresh.
 */
declare class OidcAuthProvider implements AuthProvider {
    private getToken;
    constructor(getToken: () => string | Promise<string>);
    getHeaders(): Promise<Record<string, string>>;
}

/** Auth provider interface — implementations supply headers for each request. */
interface AuthProvider {
    getHeaders(): Record<string, string> | Promise<Record<string, string>>;
    /**
     * Whether the transport may cache this provider's headers across requests.
     * Static credentials (API keys) set this true; rotating credentials that
     * the transport must re-fetch every request (OIDC bearer tokens, which the
     * consumer's callback refreshes) leave it false/undefined so an expiring
     * token never pins a stale header (CASE-569).
     */
    cacheable?: boolean;
}

interface FetchTransportConfig {
    baseUrl: string;
    auth?: AuthProvider;
    timeout?: number;
    retry?: RetryConfig;
    onAuthError?: () => void;
}
interface RetryConfig {
    maxRetries: number;
    baseDelayMs?: number;
    maxDelayMs?: number;
}
declare class FetchTransport {
    private baseUrl;
    private auth?;
    private timeout;
    private retry;
    private onAuthError?;
    private cachedAuthHeaders;
    constructor(config: FetchTransportConfig);
    setAuth(auth: AuthProvider | undefined): void;
    /**
     * Resolve auth headers for one request. Providers that opt into caching
     * (`cacheable`, e.g. a static API key) are fetched once and reused until a
     * 401/403 or setAuth() clears the cache; non-cacheable providers (OIDC,
     * whose bearer the consumer callback rotates) are re-fetched every request
     * so an expired token never pins a stale header (CASE-569).
     */
    private resolveAuthHeaders;
    request<T>(method: string, path: string, options?: {
        body?: unknown;
        params?: Record<string, unknown>;
        headers?: Record<string, string>;
        responseType?: 'json' | 'blob' | 'text';
        timeout?: number;
    }): Promise<T>;
    /**
     * Open a streaming request (used for Server-Sent Events).
     *
     * Unlike `request()`, this returns the raw `Response` so the caller can
     * read `response.body` as a `ReadableStream`. No retries — streaming
     * connections are stateful and a retry would re-deliver duplicate events.
     *
     * Throws the same `Wip*Error` taxonomy as `request()` for non-2xx
     * responses, so callers can handle 401/404/410 etc. before they start
     * reading the body.
     */
    stream(method: string, path: string, options?: {
        params?: Record<string, unknown>;
        headers?: Record<string, string>;
        signal?: AbortSignal;
    }): Promise<Response>;
    private buildUrl;
    private mapResponseError;
    private extractMessage;
}

interface BulkResultItem {
    index: number;
    status: string;
    id?: string;
    error?: string;
    /** Machine-readable error code, set when status === "error". See per-endpoint docs for the code matrix. */
    error_code?: string;
    /** Structured details for non-error statuses (e.g. compatibility diff for on_conflict=validate). */
    details?: Record<string, unknown>;
    value?: string;
    version?: number;
    is_new_version?: boolean;
    document_id?: string;
    identity_hash?: string;
    is_new?: boolean;
    warnings?: string[];
}
interface BulkResponse {
    results: BulkResultItem[];
    total: number;
    succeeded: number;
    failed: number;
    skipped?: number;
    timing?: Record<string, number>;
}
interface PaginatedResponse<T> {
    items: T[];
    total: number;
    page: number;
    page_size: number;
    pages: number;
}
interface ApiError {
    detail: string | Record<string, unknown>;
}

declare abstract class BaseService {
    protected readonly transport: FetchTransport;
    protected readonly basePath: string;
    constructor(transport: FetchTransport, basePath: string);
    protected get<T>(path: string, params?: Record<string, unknown> | object): Promise<T>;
    protected post<T>(path: string, body?: unknown, params?: Record<string, unknown> | object): Promise<T>;
    protected put<T>(path: string, body?: unknown, params?: Record<string, unknown> | object): Promise<T>;
    protected patch<T>(path: string, body?: unknown, params?: Record<string, unknown> | object): Promise<T>;
    protected del<T>(path: string, body?: unknown, params?: Record<string, unknown> | object): Promise<T>;
    protected getBlob(path: string, params?: Record<string, unknown> | object): Promise<Blob>;
    protected postFormData<T>(path: string, formData: FormData, params?: Record<string, unknown> | object): Promise<T>;
    protected stream(method: string, path: string, options?: {
        params?: Record<string, unknown>;
        signal?: AbortSignal;
        headers?: Record<string, string>;
    }): Promise<Response>;
    protected bulkWrite(path: string, items: unknown[], method?: 'POST' | 'PUT' | 'PATCH' | 'DELETE', params?: Record<string, unknown>): Promise<BulkResponse>;
    protected bulkWriteOne(path: string, item: unknown, method?: 'POST' | 'PUT' | 'PATCH' | 'DELETE', params?: Record<string, unknown>): Promise<BulkResultItem>;
}

interface TerminologyMetadata {
    source?: string;
    source_url?: string;
    version?: string;
    language: string;
    custom: Record<string, unknown>;
}
interface Terminology {
    terminology_id: string;
    namespace: string;
    value: string;
    label: string;
    description?: string;
    case_sensitive: boolean;
    allow_multiple: boolean;
    extensible: boolean;
    mutable: boolean;
    metadata: TerminologyMetadata;
    status: 'active' | 'inactive';
    term_count: number;
    created_at: string;
    created_by?: string;
    updated_at: string;
    updated_by?: string;
}
interface CreateTerminologyRequest {
    value: string;
    label: string;
    description?: string;
    namespace: string;
    case_sensitive?: boolean;
    allow_multiple?: boolean;
    extensible?: boolean;
    mutable?: boolean;
    metadata?: Partial<TerminologyMetadata>;
    created_by?: string;
}
interface UpdateTerminologyRequest {
    value?: string;
    label?: string;
    description?: string;
    case_sensitive?: boolean;
    allow_multiple?: boolean;
    extensible?: boolean;
    mutable?: boolean;
    metadata?: Partial<TerminologyMetadata>;
    updated_by?: string;
}
type TerminologyListResponse = PaginatedResponse<Terminology>;
interface TermTranslation {
    language: string;
    label: string;
    description?: string;
}
interface Term {
    term_id: string;
    namespace: string;
    terminology_id: string;
    terminology_value?: string;
    value: string;
    aliases: string[];
    label?: string;
    description?: string;
    sort_order: number;
    parent_term_id?: string;
    translations: TermTranslation[];
    metadata: Record<string, unknown>;
    status: 'active' | 'deprecated' | 'inactive';
    deprecated_reason?: string;
    replaced_by_term_id?: string;
    created_at: string;
    created_by?: string;
    updated_at: string;
    updated_by?: string;
}
interface CreateTermRequest {
    value: string;
    aliases?: string[];
    label?: string;
    description?: string;
    sort_order?: number;
    parent_term_id?: string;
    translations?: TermTranslation[];
    metadata?: Record<string, unknown>;
    created_by?: string;
}
interface UpdateTermRequest {
    value?: string;
    aliases?: string[];
    label?: string;
    description?: string;
    sort_order?: number;
    parent_term_id?: string;
    translations?: TermTranslation[];
    metadata?: Record<string, unknown>;
    updated_by?: string;
}
interface DeprecateTermRequest {
    reason: string;
    replaced_by_term_id?: string;
    updated_by?: string;
}
interface TermListResponse extends PaginatedResponse<Term> {
    terminology_id: string;
    terminology_value: string;
}
interface ImportTerminologyRequest {
    terminology: CreateTerminologyRequest;
    terms: CreateTermRequest[];
    relations?: Array<{
        source_term_value: string;
        target_term_value: string;
        relation_type: string;
        target_terminology_value?: string;
    }>;
    options?: {
        skip_duplicates?: boolean;
        update_existing?: boolean;
    };
}
interface ExportTerminologyResponse {
    terminology: Terminology;
    terms: Term[];
    export_date: string;
    format: string;
    version: string;
    relations?: Array<{
        source_term_value: string;
        target_term_value: string;
        relation_type: string;
        metadata?: Record<string, unknown>;
        source_terminology_id?: string;
        target_terminology_id?: string;
    }>;
}
interface ValidateValueRequest {
    terminology_id?: string;
    terminology_value?: string;
    value: string;
}
interface ValidateValueResponse {
    valid: boolean;
    terminology_id: string;
    terminology_value: string;
    value: string;
    matched_term?: Term;
    matched_via?: 'value' | 'alias';
    suggestion?: Term;
    error?: string;
}
interface BulkValidateRequest {
    items: ValidateValueRequest[];
}
interface BulkValidateResponse {
    results: ValidateValueResponse[];
    total: number;
    valid_count: number;
    invalid_count: number;
}
interface AuditLogEntry {
    term_id: string;
    terminology_id: string;
    action: 'created' | 'updated' | 'deprecated' | 'deleted';
    changed_at: string;
    changed_by: string | null;
    changed_fields: string[];
    previous_values: Record<string, unknown>;
    new_values: Record<string, unknown>;
    comment: string | null;
}
interface AuditLogResponse {
    items: AuditLogEntry[];
    total: number;
    page: number;
    page_size: number;
}

interface TermRelation {
    namespace: string;
    source_term_id: string;
    target_term_id: string;
    relation_type: string;
    relation_value?: string;
    source_term_value?: string;
    source_term_label?: string;
    target_term_value?: string;
    target_term_label?: string;
    source_terminology_id?: string;
    target_terminology_id?: string;
    metadata: Record<string, unknown>;
    status: string;
    created_at: string;
    created_by?: string;
}
type TermRelationListResponse = PaginatedResponse<TermRelation>;
interface CreateTermRelationRequest {
    /**
     * Canonical UUID, fully qualified 'ns:terminology:value', or — with
     * source_terminology set — the opaque raw term value (never
     * colon-parsed). The ambiguous 2-part 'TERMINOLOGY:VALUE' shorthand
     * is rejected by the platform (422).
     */
    source_term_id: string;
    target_term_id: string;
    /**
     * Terminology scoping a value-form source_term_id. Per-item because a
     * relation's two endpoints may live in different terminologies.
     */
    source_terminology?: string;
    target_terminology?: string;
    relation_type: string;
    metadata?: Record<string, unknown>;
    created_by?: string;
}
interface DeleteTermRelationRequest {
    source_term_id: string;
    target_term_id: string;
    source_terminology?: string;
    target_terminology?: string;
    relation_type: string;
    hard_delete?: boolean;
}
interface TraversalNode {
    term_id: string;
    value?: string;
    terminology_id?: string;
    depth: number;
    path: string[];
}
interface TraversalResponse {
    term_id: string;
    relation_type: string;
    direction: string;
    nodes: TraversalNode[];
    total: number;
    max_depth_reached: boolean;
}

declare class DefStoreService extends BaseService {
    constructor(transport: FetchTransport);
    listTerminologies(params?: {
        page?: number;
        page_size?: number;
        status?: string;
        value?: string;
        namespace?: string;
        sort_by?: string;
        sort_order?: 'asc' | 'desc';
    }): Promise<TerminologyListResponse>;
    getTerminology(id: string): Promise<Terminology>;
    createTerminology(data: CreateTerminologyRequest): Promise<BulkResultItem>;
    createTerminologies(data: CreateTerminologyRequest[]): Promise<BulkResponse>;
    updateTerminology(id: string, data: UpdateTerminologyRequest): Promise<BulkResultItem>;
    deleteTerminology(id: string, options?: {
        force?: boolean;
        hardDelete?: boolean;
    }): Promise<BulkResultItem>;
    listTerms(terminologyId: string, params?: {
        page?: number;
        page_size?: number;
        status?: string;
        search?: string;
        namespace?: string;
        sort_by?: string;
        sort_order?: 'asc' | 'desc';
    }): Promise<TermListResponse>;
    /**
     * Term identifiers accept a canonical UUID, the fully qualified
     * 'ns:terminology:value' form, or — with the terminology option set —
     * the opaque raw term value (never colon-parsed). The ambiguous
     * 2-part 'TERMINOLOGY:VALUE' shorthand is rejected (422).
     */
    getTerm(termId: string, options?: {
        namespace?: string;
        terminology?: string;
    }): Promise<Term>;
    createTerm(terminologyId: string, data: CreateTermRequest, options: {
        namespace: string;
    }): Promise<BulkResultItem>;
    createTerms(terminologyId: string, terms: CreateTermRequest[], options: {
        namespace: string;
        batch_size?: number;
        registry_batch_size?: number;
    }): Promise<BulkResponse>;
    /**
     * Term write identifiers accept a canonical UUID, the fully qualified
     * 'ns:terminology:value' form, or — with the terminology option set —
     * the opaque raw term value (never colon-parsed). The ambiguous
     * 2-part 'TERMINOLOGY:VALUE' shorthand is rejected (422).
     */
    updateTerm(termId: string, data: UpdateTermRequest, options?: {
        namespace?: string;
        terminology?: string;
    }): Promise<BulkResultItem>;
    /**
     * The terminology option scopes term_id AND replaced_by_term_id — a
     * replacement lives in the same vocabulary; a cross-terminology
     * pointer must be a UUID or fully qualified.
     */
    deprecateTerm(termId: string, data: DeprecateTermRequest, options?: {
        namespace?: string;
        terminology?: string;
    }): Promise<BulkResultItem>;
    deleteTerm(termId: string, options?: {
        hardDelete?: boolean;
        namespace?: string;
        terminology?: string;
    }): Promise<BulkResultItem>;
    importTerminology(data: ImportTerminologyRequest): Promise<{
        terminology: Terminology;
        terms_result: BulkResponse;
        relations_result?: {
            total: number;
            created: number;
            skipped: number;
            errors: number;
            error_samples: string[];
        };
    }>;
    exportTerminology(terminologyId: string, options?: {
        format?: 'json' | 'csv';
        includeInactive?: boolean;
        includeRelations?: boolean;
        includeMetadata?: boolean;
        languages?: string[];
    }): Promise<ExportTerminologyResponse | string>;
    importOntology(data: Record<string, unknown>, options: {
        namespace: string;
        terminology_value?: string;
        terminology_label?: string;
        prefix_filter?: string;
        include_deprecated?: boolean;
        max_synonyms?: number;
        batch_size?: number;
        registry_batch_size?: number;
        relation_batch_size?: number;
        skip_duplicates?: boolean;
        update_existing?: boolean;
    }): Promise<{
        terminology: {
            terminology_id: string;
            value: string;
            label: string;
            status: string;
        };
        terms: {
            total: number;
            created: number;
            skipped: number;
            errors: number;
        };
        relations: {
            total: number;
            created: number;
            skipped: number;
            errors: number;
            predicate_distribution: Record<string, number>;
            error_samples: string[];
        };
        elapsed_seconds: number;
    }>;
    validateValue(data: ValidateValueRequest): Promise<ValidateValueResponse>;
    bulkValidate(data: BulkValidateRequest): Promise<BulkValidateResponse>;
    listTermRelations(params: {
        term_id: string;
        direction?: string;
        relation_type?: string;
        namespace?: string;
        /** Scopes a value-form term_id as the opaque raw value (never colon-parsed). */
        terminology?: string;
        page?: number;
        page_size?: number;
    }): Promise<TermRelationListResponse>;
    listAllTermRelations(params?: {
        namespace?: string;
        relation_type?: string;
        status?: string;
        page?: number;
        page_size?: number;
    }): Promise<TermRelationListResponse>;
    createTermRelations(items: CreateTermRelationRequest[], namespace: string): Promise<BulkResponse>;
    deleteTermRelations(items: DeleteTermRelationRequest[], namespace: string): Promise<BulkResponse>;
    getAncestors(termId: string, params?: {
        relation_type?: string;
        namespace?: string;
        /** Scopes a value-form termId as the opaque raw value (never colon-parsed). */
        terminology?: string;
        max_depth?: number;
    }): Promise<TraversalResponse>;
    getDescendants(termId: string, params?: {
        relation_type?: string;
        namespace?: string;
        terminology?: string;
        max_depth?: number;
    }): Promise<TraversalResponse>;
    getParents(termId: string, params?: {
        relation_type?: string;
        namespace?: string;
        terminology?: string;
    }): Promise<TermRelation[]>;
    getChildren(termId: string, params?: {
        relation_type?: string;
        namespace?: string;
        terminology?: string;
    }): Promise<TermRelation[]>;
    getTerminologyAuditLog(terminologyId: string, params?: {
        action?: string;
        page?: number;
        page_size?: number;
    }): Promise<AuditLogResponse>;
    getTermAuditLog(termId: string, params?: {
        action?: string;
        namespace?: string;
        /** Scopes a value-form termId as the opaque raw value (never colon-parsed). */
        terminology?: string;
        page?: number;
        page_size?: number;
    }): Promise<AuditLogResponse>;
    getRecentAuditLog(params?: {
        action?: string;
        page?: number;
        page_size?: number;
    }): Promise<AuditLogResponse>;
}

type FieldType = 'string' | 'number' | 'integer' | 'boolean' | 'date' | 'datetime' | 'term' | 'reference' | 'file' | 'object' | 'array';
type ReferenceType = 'document' | 'term' | 'terminology' | 'template';
type VersionStrategy = 'latest' | 'pinned';
type SemanticType = 'email' | 'url' | 'latitude' | 'longitude' | 'percentage' | 'duration' | 'geo_point';
interface FieldValidation {
    pattern?: string;
    min_length?: number;
    max_length?: number;
    minimum?: number;
    maximum?: number;
    enum?: unknown[];
}
interface FileFieldConfig {
    allowed_types: string[];
    max_size_mb: number;
    multiple: boolean;
    max_files?: number;
}
interface FieldDefinition {
    name: string;
    label: string;
    type: FieldType;
    mandatory: boolean;
    default_value?: unknown;
    terminology_ref?: string;
    template_ref?: string;
    /** Pinned version of template_ref. Backend (CASE-493) requires this when template_ref is set. */
    template_ref_version?: number;
    reference_type?: ReferenceType;
    target_templates?: string[];
    target_terminologies?: string[];
    version_strategy?: VersionStrategy;
    file_config?: FileFieldConfig;
    array_item_type?: FieldType;
    array_terminology_ref?: string;
    array_template_ref?: string;
    /** Pinned version of array_template_ref. Backend (CASE-493) requires this when array_template_ref is set. */
    array_template_ref_version?: number;
    array_file_config?: FileFieldConfig;
    validation?: FieldValidation;
    semantic_type?: SemanticType;
    include_subtypes?: boolean;
    full_text_indexed?: boolean;
    inherited?: boolean;
    inherited_from?: string;
    metadata: Record<string, unknown>;
}
type RuleType = 'conditional_required' | 'conditional_value' | 'mutual_exclusion' | 'dependency' | 'pattern' | 'range';
type ConditionOperator = 'equals' | 'not_equals' | 'in' | 'not_in' | 'exists' | 'not_exists';
interface Condition {
    field: string;
    operator: ConditionOperator;
    value?: unknown;
}
interface ValidationRule {
    type: RuleType;
    description?: string;
    conditions: Condition[];
    target_field?: string;
    target_fields?: string[];
    required?: boolean;
    allowed_values?: unknown[];
    pattern?: string;
    minimum?: number;
    maximum?: number;
    error_message?: string;
}
type SyncStrategy = 'latest_only' | 'all_versions';
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
type TemplateUsage = 'entity' | 'reference' | 'relationship';
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
interface CrossVersionView {
    /** Version tables the view spans. Server default is 'all'. */
    versions: 'all' | number[];
    /**
     * Target column → optional source. `{ from: old }` maps a renamed column
     * (declared renames land here). `{}` or `null` means the column keeps its
     * own name in the versions that have it, and is NULL elsewhere.
     */
    columns: Record<string, {
        from?: string;
    } | null>;
}
interface ReportingConfig {
    sync_enabled: boolean;
    sync_strategy: SyncStrategy;
    table_name?: string;
    include_metadata: boolean;
    flatten_arrays: boolean;
    max_array_elements: number;
    /**
     * Opt-in cross-version view config. Stored pass-through on the template;
     * the shape is owned and validated by reporting-sync, which builds the
     * view. Absent / null means the bare name exposes the identity core only.
     */
    cross_version_view?: CrossVersionView | null;
}
interface TemplateMetadata {
    domain?: string;
    category?: string;
    tags: string[];
    custom: Record<string, unknown>;
}
interface Template {
    template_id: string;
    namespace: string;
    value: string;
    label: string;
    description?: string;
    version: number;
    extends?: string;
    extends_version?: number;
    identity_fields: string[];
    /**
     * Fields to surface in peer/header projection contexts (CASE-343).
     * Bare names target `data.<name>`; `metadata.custom.<name>` paths
     * are allowed for audit fields. Empty → the platform's projection
     * falls back to `identity_fields`. The relationships endpoint's
     * `?include=peers` projection reads this; future list / summary
     * endpoints may consume it too.
     */
    header_fields?: string[];
    /**
     * Usage class: entity (default), reference, or relationship.
     * Immutable after creation. Relationship templates ("edge types")
     * additionally require source_templates + target_templates and
     * source_ref / target_ref reference fields.
     */
    usage?: TemplateUsage;
    /**
     * Template values allowed as the source endpoint of an edge.
     * Set only on relationship templates; empty / absent on entity and
     * reference templates.
     */
    source_templates?: string[];
    /**
     * Template values allowed as the target endpoint of an edge.
     * Set only on relationship templates.
     */
    target_templates?: string[];
    /**
     * True (default) = updates create new versions; false = overwrite
     * in place. Currently only available on relationship templates.
     * Immutable after creation. See PoNIF #8.
     */
    versioned?: boolean;
    /**
     * Field renames this version declared relative to the previous one, as
     * `{new_field: old_field}`. Persisted, so it comes back on read — a
     * template editor renders the declaration it was created with.
     */
    renames?: Record<string, string> | null;
    fields: FieldDefinition[];
    rules: ValidationRule[];
    metadata: TemplateMetadata;
    reporting?: ReportingConfig;
    status: 'draft' | 'active' | 'inactive';
    created_at: string;
    created_by?: string;
    updated_at: string;
    updated_by?: string;
}
interface CreateTemplateRequest {
    value: string;
    label: string;
    description?: string;
    template_id?: string;
    version?: number;
    namespace: string;
    extends?: string;
    extends_version?: number;
    identity_fields?: string[];
    /**
     * Peer/header-projection fields (CASE-343). Bare names target data.*,
     * `metadata.custom.<name>` paths allowed. Empty → projection falls
     * back to identity_fields.
     */
    header_fields?: string[];
    /** Usage class — defaults to 'entity' on the server when omitted. */
    usage?: TemplateUsage;
    /** Required when usage='relationship'; ignored otherwise. */
    source_templates?: string[];
    /** Required when usage='relationship'; ignored otherwise. */
    target_templates?: string[];
    /** Defaults to true. Immutable after creation. See PoNIF #8. */
    versioned?: boolean;
    /**
     * Field renames relative to the previous version, `{new_field: old_field}`.
     * Validated against the version being renamed from (the old name existed
     * and is gone, the new one is new, types match, identity fields excluded)
     * and rejected on a first version. A declared rename migrates losslessly
     * and maps in reporting; an undeclared one strands the old column's data.
     */
    renames?: Record<string, string>;
    fields?: FieldDefinition[];
    rules?: ValidationRule[];
    metadata?: Partial<TemplateMetadata>;
    reporting?: Partial<ReportingConfig>;
    created_by?: string;
    validate_references?: boolean;
    status?: string;
}
interface UpdateTemplateRequest {
    value?: string;
    label?: string;
    description?: string;
    extends?: string;
    extends_version?: number;
    identity_fields?: string[];
    /** Update peer/header-projection fields (CASE-343). */
    header_fields?: string[];
    /**
     * Field renames the new version declares relative to the current one,
     * `{new_field: old_field}`. This is the path an interactive editor takes —
     * renaming a field on an existing template — so it matters here as much as
     * on create. Same validation as the create path.
     */
    renames?: Record<string, string>;
    fields?: FieldDefinition[];
    rules?: ValidationRule[];
    metadata?: Partial<TemplateMetadata>;
    reporting?: Partial<ReportingConfig>;
    updated_by?: string;
}
type TemplateListResponse = PaginatedResponse<Template>;
interface ValidateTemplateRequest {
    check_terminologies?: boolean;
    check_templates?: boolean;
}
interface ValidateTemplateResponse {
    valid: boolean;
    template_id: string;
    errors: Array<{
        field: string;
        code: string;
        message: string;
    }>;
    warnings: Array<{
        field: string;
        code: string;
        message: string;
    }>;
    will_also_activate?: string[];
}
interface TemplateUpdateResponse {
    template_id: string;
    value: string;
    version: number;
    is_new_version: boolean;
    previous_version?: number;
}
interface ActivationDetail {
    template_id: string;
    value: string;
    status: string;
}
interface ActivateTemplateResponse {
    activated: string[];
    activation_details: ActivationDetail[];
    total_activated: number;
    errors: Array<{
        field: string;
        code: string;
        message: string;
    }>;
    warnings: Array<{
        field: string;
        code: string;
        message: string;
    }>;
}
interface CascadeResult {
    value: string;
    old_template_id: string;
    new_template_id?: string;
    new_version?: number;
    status: string;
    error?: string;
}
interface CascadeResponse {
    parent_template_id: string;
    parent_value: string;
    parent_version: number;
    total: number;
    updated: number;
    unchanged: number;
    failed: number;
    results: CascadeResult[];
}
/**
 * Live-document impact of a version event, computed advisory-side from
 * document-store.
 *
 * `status: 'unavailable'` is a real answer, not an error: document-store
 * could not be reached, so the counts are unknown. It is deliberately never
 * a silent zero, which would read as "no documents affected".
 */
interface TemplateVersionImpact {
    status: 'ok' | 'unavailable';
    /** Present when status === 'ok'. */
    total_live_docs?: number;
    /** Live document count keyed by the template version they validated against. */
    docs_per_version?: Record<string, number>;
    /** Per-field count of live documents where the field is non-empty. */
    field_nonempty_counts?: Record<string, number>;
    /** Present when status === 'unavailable' — why the counts are missing. */
    reason?: string;
}
/**
 * Whether the platform can offer to move existing documents onto the new
 * version, and how.
 */
interface TemplateMigrationOffer {
    /**
     * true = offerable; false = needs app-side data decisions (type changes,
     * newly-required fields, removed fields carrying live data); null =
     * removed fields present but the counts were unavailable, so run the
     * dry-run for a per-document readiness report.
     */
    eligible: boolean | null;
    reason: string;
    /** The operation to run — the migrate dry-run/apply cycle. */
    via: string;
}
/**
 * `details` on a bulk result item that minted a new template version: the
 * schema diff, plus the consequences of having minted it.
 *
 * Both write paths carry this — create-as-upsert (`POST /templates`) and
 * update (`PUT /templates`). Older backends attached it on create only, so
 * treat it as optional and narrow with `asVersionEventDetails`.
 */
interface TemplateVersionEventDetails {
    added_optional: string[];
    added_required: string[];
    removed: string[];
    changed_type: Array<{
        name: string;
        old_type: string;
        new_type: string;
    }>;
    made_required: string[];
    modified_existing: string[];
    identity_changed: {
        old: string[];
        new: string[];
    } | null;
    relationship_refs_changed: unknown | null;
    impact: TemplateVersionImpact;
    migration: TemplateMigrationOffer;
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
declare function asVersionEventDetails(details: Record<string, unknown> | undefined | null): TemplateVersionEventDetails | null;

declare class TemplateStoreService extends BaseService {
    constructor(transport: FetchTransport);
    listTemplates(params?: {
        page?: number;
        page_size?: number;
        status?: string;
        extends?: string;
        value?: string;
        latest_only?: boolean;
        namespace?: string;
        sort_by?: string;
        sort_order?: 'asc' | 'desc';
    }): Promise<TemplateListResponse>;
    getTemplate(id: string, version?: number): Promise<Template>;
    getTemplateRaw(id: string, version?: number): Promise<Template>;
    getTemplateByValue(value: string, opts?: {
        namespace?: string;
    }): Promise<Template>;
    getTemplateByValueRaw(value: string, namespace: string): Promise<Template>;
    getTemplateVersions(value: string, opts?: {
        namespace?: string;
    }): Promise<TemplateListResponse>;
    getTemplateByValueAndVersion(value: string, version: number, opts?: {
        namespace?: string;
    }): Promise<Template>;
    getTemplateVersionsById(templateId: string): Promise<TemplateListResponse>;
    /**
     * Create a single template.
     *
     * @param data - The template definition.
     * @param options - Optional behavior flags.
     * @param options.onConflict - How to handle a value collision in the same
     *   namespace. `'error'` (default) treats it as an error. `'validate'` makes
     *   the call idempotent for app bootstrap: identical schema returns
     *   `status='unchanged'`; compatible (added optional fields only) bumps to
     *   version N+1; incompatible throws `WipBulkItemError` with
     *   `errorCode='incompatible_schema'` and a structured `details` diff.
     */
    createTemplate(data: CreateTemplateRequest, options?: {
        onConflict?: 'error' | 'validate';
    }): Promise<BulkResultItem>;
    createTemplates(data: CreateTemplateRequest[], options?: {
        onConflict?: 'error' | 'validate';
    }): Promise<BulkResponse>;
    updateTemplate(id: string, data: UpdateTemplateRequest): Promise<BulkResultItem>;
    deleteTemplate(id: string, options?: {
        updatedBy?: string;
        version?: number;
        force?: boolean;
        hardDelete?: boolean;
    }): Promise<BulkResultItem>;
    validateTemplate(id: string, request?: ValidateTemplateRequest): Promise<ValidateTemplateResponse>;
    getChildren(id: string): Promise<TemplateListResponse>;
    getDescendants(id: string): Promise<TemplateListResponse>;
    activateTemplate(id: string, options: {
        namespace: string;
        dry_run?: boolean;
    }): Promise<ActivateTemplateResponse>;
    /**
     * Reactivate a soft-deleted (inactive) template version (CASE-498).
     *
     * The inverse of soft-delete-by-version (`deleteTemplate(id, { version })`):
     * restores a specific frozen version to active so documents pinned to it
     * can be updated again. Distinct from `activateTemplate`, which is draft-only
     * and addresses the latest version — `version` is required here and targets a
     * known frozen version (there is no "latest" default). Idempotent on an
     * already-active version; a draft version is rejected by the backend.
     */
    reactivateTemplate(id: string, version: number, options: {
        namespace: string;
    }): Promise<Template>;
    cascadeTemplate(id: string): Promise<CascadeResponse>;
    /**
     * Additively widen an edge type's allowed endpoint set (CASE-515).
     *
     * Adds source and/or target endpoint templates to an existing relationship
     * template (PoNIF #7) in place, preserving every existing edge — the
     * supported alternative to the delete+recreate that would strand them.
     * Endpoints are append-only: this only ADDS (removal stays unsupported).
     * Each new endpoint must be a real template; idempotent on already-allowed
     * endpoints. No reindex / reporting migration — the relationship indexes and
     * reporting columns are generic.
     */
    addEdgeTypeEndpoints(id: string, options: {
        namespace: string;
        addSourceTemplates?: string[];
        addTargetTemplates?: string[];
    }): Promise<Template>;
}

type DocumentStatus = 'active' | 'inactive' | 'archived';
interface DocumentMetadata {
    source_system: string | null;
    warnings: string[];
    custom: Record<string, unknown>;
}
interface TermReference {
    field_path: string;
    term_id: string;
    terminology_ref?: string;
    matched_via?: string;
}
interface Reference {
    field_path: string;
    reference_type: 'document' | 'term' | 'terminology' | 'template';
    lookup_value: string;
    version_strategy?: 'latest' | 'pinned';
    resolved: {
        document_id?: string;
        identity_hash?: string;
        template_id?: string;
        version?: number;
        term_id?: string;
        terminology_value?: string;
        matched_via?: string;
        terminology_id?: string;
        template_value?: string;
    };
}
interface Document {
    document_id: string;
    namespace: string;
    template_id: string;
    template_value?: string;
    template_version: number;
    identity_hash: string;
    version: number;
    data: Record<string, unknown>;
    term_references: TermReference[];
    references: Reference[];
    file_references: Array<Record<string, unknown>>;
    status: DocumentStatus;
    created_at: string;
    created_by: string | null;
    updated_at: string;
    updated_by: string | null;
    metadata: DocumentMetadata;
    is_latest_version?: boolean;
    latest_version?: number;
    /**
     * Compact projection of a related entity, attached when the
     * relationships endpoint is called with `?include=peers` (CASE-303 /
     * CASE-343 / CASE-348). Absent on documents returned by other
     * endpoints. `null` when no related entity could be projected (e.g.,
     * the target template has no `header_fields`, no `identity_fields`,
     * and no legacy fallback).
     */
    peer?: PeerProjection | null;
}
interface CreateDocumentRequest {
    template_id: string;
    template_version?: number;
    document_id?: string;
    version?: number;
    namespace: string;
    data: Record<string, unknown>;
    created_by?: string;
    metadata?: Record<string, unknown>;
    synonyms?: Array<Record<string, unknown>>;
}
interface DocumentCreateResponse {
    document_id: string;
    namespace: string;
    template_id: string;
    template_value?: string;
    identity_hash: string;
    version: number;
    is_new: boolean;
    previous_version?: number;
    warnings: string[];
}
interface DocumentQueryParams {
    page?: number;
    page_size?: number;
    template_id?: string;
    template_value?: string;
    status?: DocumentStatus;
    latest_only?: boolean;
    cursor?: string;
    namespace?: string;
    sort_by?: string;
    sort_order?: 'asc' | 'desc';
}
interface DocumentListResponse extends PaginatedResponse<Document> {
    next_cursor?: string;
}
type QueryFilterOperator = 'eq' | 'ne' | 'gt' | 'gte' | 'lt' | 'lte' | 'in' | 'nin' | 'exists' | 'regex';
interface QueryFilter {
    /** Field path to filter on (e.g., 'data.account', 'data.status', 'template_id') */
    field: string;
    /** Comparison operator. Default: 'eq' */
    operator?: QueryFilterOperator;
    /** Value to compare against */
    value: unknown;
}
interface DocumentQueryRequest {
    /** Filter conditions (AND logic) */
    filters?: QueryFilter[];
    /** Filter by template ID */
    template_id?: string;
    /** Filter by status */
    status?: DocumentStatus;
    page?: number;
    page_size?: number;
    /** Field to sort by. Default: 'created_at' */
    sort_by?: string;
    /** Sort order. Default: 'desc' */
    sort_order?: 'asc' | 'desc';
}
interface DocumentValidationResponse {
    valid: boolean;
    errors: Array<{
        field: string | null;
        code: string;
        message: string;
        details?: Record<string, unknown>;
    }>;
    warnings: string[];
    identity_hash: string | null;
    template_version: number | null;
    term_references?: Array<Record<string, unknown>>;
    references?: Array<Record<string, unknown>>;
    file_references?: Array<Record<string, unknown>>;
}
/**
 * Single item in a PATCH /documents bulk request.
 *
 * Applies an RFC 7396 JSON Merge Patch to the document's `data`. Identity fields
 * cannot be changed (use POST to create a new document instead).
 */
interface PatchDocumentRequest {
    /** Canonical document_id (UUID) or registered synonym. Synonyms are resolved server-side. */
    document_id: string;
    /**
     * RFC 7396 JSON Merge Patch applied to the document's `data` field.
     * Objects deep-merge, arrays replace, `null` deletes the key.
     * Pass `{}` for a metadata-only patch.
     */
    patch: Record<string, unknown>;
    /**
     * Optional RFC 7396 JSON Merge Patch applied to the document's
     * `metadata.custom`. Metadata is non-identity document content — a
     * metadata change creates a new version like any other change, but never
     * feeds the identity hash. Platform-owned metadata (warnings,
     * source_system) cannot be addressed. Omitted = metadata carries forward.
     */
    metadata_patch?: Record<string, unknown>;
    /**
     * Optional optimistic concurrency control. If supplied, the patch fails with
     * `concurrency_conflict` unless the current document version matches.
     */
    if_match?: number;
}
interface ValidateDocumentRequest {
    template_id: string;
    namespace: string;
    data: Record<string, unknown>;
}
/** Bulk validate request (CASE-419): one template, many data payloads. */
interface ValidateDocumentsRequest {
    template_id: string;
    namespace: string;
    /** Specific template version to validate against. Default: latest. */
    template_version?: number;
    /** Document data payloads, each shaped like the singular validate `data`. */
    items: Array<Record<string, unknown>>;
}
/** Bulk validate response (CASE-419): one result per item, in input order. */
interface BulkValidationResponse {
    results: DocumentValidationResponse[];
}
/**
 * One row of a document's version history.
 *
 * `created_at` is the ENTITY's creation time — when version 1 was written — so
 * it is identical on every row and does NOT tell you when this particular
 * version was persisted. `updated_at` does, which is what makes the history a
 * usable audit trail. Code that sorts or displays version history by
 * `created_at` will not order anything; read `updated_at` instead.
 *
 * `updated_at` / `updated_by` are absent on documents written before the
 * platform distinguished the two stamps, hence optional.
 */
interface DocumentVersionSummary {
    document_id: string;
    version: number;
    status: DocumentStatus;
    created_at: string;
    created_by: string | null;
    updated_at?: string | null;
    updated_by?: string | null;
}
interface DocumentVersionResponse {
    identity_hash: string;
    current_version: number;
    versions: DocumentVersionSummary[];
}
interface TableColumn {
    name: string;
    label: string;
    type: string;
    is_array: boolean;
    is_flattened: boolean;
}
interface TableViewResponse {
    template_id: string;
    template_value: string;
    template_label: string;
    columns: TableColumn[];
    rows: Record<string, unknown>[];
    total_documents: number;
    total_rows: number;
    page: number;
    page_size: number;
    pages: number;
    array_handling: 'none' | 'flattened' | 'json';
}
interface TableViewParams {
    status?: DocumentStatus;
    page?: number;
    page_size?: number;
    max_cross_product?: number;
}
interface TemplateFacet {
    template_id: string;
    template_value: string | null;
    /**
     * The template's OWN namespace — may differ from the queried namespace
     * (shared / cross-namespace templates). Null when the template could not
     * be fetched.
     */
    template_namespace: string | null;
    /** Distinct logical documents (version rows collapse before counting). */
    document_count: number;
}
interface TemplateFacetsResponse {
    namespace: string;
    facets: TemplateFacet[];
}
interface TemplateFacetsParams {
    /** Omittable only under a single-namespace API key. */
    namespace?: string;
    /** Document status to count; 'all' disables the default active-only filter. */
    status?: DocumentStatus | 'all';
}
interface ImportPreviewResponse {
    headers: string[];
    rows: Record<string, unknown>[];
    format: string;
    error?: string;
}
interface ImportDocumentsOptions {
    template_id: string;
    column_mapping: Record<string, string>;
    namespace: string;
    skip_errors?: boolean;
}
interface ImportDocumentResult {
    row: number;
    document_id: string;
    version: number;
    is_new: boolean;
}
interface ImportDocumentError {
    row: number;
    error: string;
    data: Record<string, string>;
}
interface ImportDocumentsResponse {
    total_rows: number;
    succeeded: number;
    failed: number;
    skipped: number;
    results: ImportDocumentResult[];
    errors: ImportDocumentError[];
}
type ReplayStatus = 'pending' | 'running' | 'paused' | 'completed' | 'cancelled' | 'failed';
interface ReplayFilter {
    template_id?: string;
    template_value?: string;
    namespace?: string;
    status?: string;
}
interface ReplayRequest {
    filter?: ReplayFilter;
    throttle_ms?: number;
    batch_size?: number;
}
interface ReplaySessionResponse {
    session_id: string;
    status: ReplayStatus;
    total_count: number;
    published: number;
    throttle_ms: number;
    message: string;
}
/**
 * Params for `GET /api/document-store/documents/{id}/relationships`.
 *
 * Returns relationship documents (templates with `usage: 'relationship'`)
 * that point at (incoming) or from (outgoing) the given document.
 * Backed by Mongo indexes on `(template_id, data.source_ref)` and
 * `(template_id, data.target_ref)`.
 */
interface DocumentRelationshipsParams {
    /** `incoming` | `outgoing` | `both`. Default `both`. */
    direction?: 'incoming' | 'outgoing' | 'both';
    /** Comma-separated relationship template values. Default: all. */
    template?: string;
    /** Defaults to the seed document's namespace. */
    namespace?: string;
    /** Default true — exclude inactive/archived rel docs. */
    active_only?: boolean;
    page?: number;
    /** Default 50, capped at 500. */
    page_size?: number;
    /**
     * Comma-separated optional inclusions. Currently supports:
     *   - `peers` — embeds a PeerProjection on each item (CASE-303 / CASE-343)
     */
    include?: string;
}
/**
 * Compact projection of a peer entity document, returned on relationship
 * items when `?include=peers` is set (CASE-303, extended CASE-343).
 *
 * The fields surfaced in `data` and `metadata` are determined by the peer
 * template's `header_fields` (or `identity_fields` fallback). Legacy
 * templates with neither declared fall back to `{title, doc_status}`.
 */
interface PeerProjection {
    document_id: string;
    namespace: string;
    template_id: string;
    template_value?: string | null;
    status: 'active' | 'inactive' | 'archived' | 'deleted';
    /**
     * Projected data fields per the peer template's `header_fields`
     * (or `identity_fields` fallback).
     */
    data: Record<string, unknown>;
    /**
     * Projected metadata fields. Only populated when the peer template's
     * `header_fields` references `metadata.custom.<name>` paths
     * (CASE-343). Shape is `{custom: {<name>: <value>, ...}}` when
     * present, otherwise null/undefined.
     */
    metadata?: {
        custom: Record<string, unknown>;
    } | null;
}
/** One node in a document-relationship traversal result (CASE-296). */
interface DocumentTraverseNode {
    document_id: string;
    template_id: string;
    template_value?: string | null;
    namespace: string;
    /** Hops from the seed (0 = seed itself). */
    depth: number;
    /** Document_id of the relationship doc traversed to reach this node; null for the seed. */
    via_relationship?: string | null;
    /** Chain of document_ids from seed (exclusive) to this node (inclusive). */
    path: string[];
}
/**
 * Response for `GET /api/document-store/documents/{id}/traverse`.
 *
 * BFS expansion through relationship documents, capped at depth=10 and
 * max_nodes=1000. When a cap fires, `truncated` is true.
 */
interface DocumentTraverseResponse {
    seed_document_id: string;
    /** `outgoing` | `incoming` | `both`. */
    direction: string;
    depth: number;
    /** Relationship template values used to constrain traversal; empty = all. */
    types_filter: string[];
    nodes: DocumentTraverseNode[];
    total_nodes: number;
    /** True if a depth-cap or expansion-cap stopped traversal early. */
    truncated: boolean;
}
/** Params for `GET /api/document-store/documents/{id}/traverse` (CASE-296). */
interface DocumentTraverseParams {
    /** 1..10. Default 1. */
    depth?: number;
    /** Comma-separated relationship template values. Default: all. */
    types?: string;
    /** `outgoing` | `incoming` | `both`. Default `outgoing`. */
    direction?: 'outgoing' | 'incoming' | 'both';
    /** Defaults to the seed document's namespace. */
    namespace?: string;
}
/**
 * Request for `POST /api/document-store/documents/migrate`.
 *
 * Re-pins every active document on `from_version` to `to_version`,
 * identity-preserving only — the two template versions must declare the same
 * identity_fields, or the operation is rejected (an identity-changing move is
 * a fork, not a migrate). No data transformation happens; per-document data
 * prep is the caller's job while the source version is still writable.
 */
interface DocumentMigrateRequest {
    /** Template to migrate (canonical UUID or registered value/synonym). */
    template_id: string;
    /** Source version documents are pinned to. May be inactive (frozen). */
    from_version: number;
    /** Target version to re-pin to. Must be active. */
    to_version: number;
    /**
     * Default true: report per-document readiness without writing. A dry-run
     * with failed === 0 guarantees a successful apply (barring concurrent writes).
     */
    dry_run?: boolean;
}
/**
 * Bulk-first migrate result — always HTTP 200, per-document outcome in
 * `results` (status `updated` or `error`). When `dry_run` is true the
 * statuses are PROJECTED — nothing was written.
 */
interface DocumentMigrateResponse extends BulkResponse {
    dry_run: boolean;
    template_id: string;
    from_version: number;
    to_version: number;
}

/**
 * Types for the document-store backup/restore endpoints (CASE-23 Phase 3 STEP 7).
 *
 * These mirror the wire contract defined by
 * `components/document-store/src/document_store/models/backup_job.py`.
 *
 * Guardrail 2: `BackupProgressMessage` is the SSE wire envelope. It is
 * deliberately decoupled from the internal `wip_toolkit.models.ProgressEvent`
 * so a future implementation can replace the toolkit without breaking clients.
 */
type BackupJobKind = 'backup' | 'restore' | 'validate';
type BackupJobStatus = 'pending' | 'running' | 'complete' | 'failed';
/**
 * Restore mode.
 *
 * `'restore'` requires every target namespace to be EMPTY and inserts the
 * archive wholesale, preserving every canonical id.
 *
 * `'merge'` takes the archive as a delta against a namespace that already
 * holds data, also preserving ids. It may target a differently-named
 * namespace, provided the archive's ids are not already registered here.
 *
 * `'fresh'` keeps nothing: every entity is registered anew with a
 * Registry-minted id and every reference between them is rewritten, which is
 * what lets a namespace be restored BESIDE the one it came from — two live
 * copies cannot share a canonical id. Requires `target_namespace`, and that
 * namespace must be empty.
 */
type RestoreMode = 'restore' | 'merge' | 'fresh';
/**
 * What a merge does when the target already holds a document's identity.
 * `'skip'` (default) keeps the target's version. `'overwrite'` appends the
 * archive's LATEST version on top of the target's head, adopting the
 * target's document_id — both histories survive, and the archive's is not
 * spliced in. On a `versioned: false` template it replaces the single
 * version in place instead, matching that template's own lifecycle.
 *
 * `'newer'` does what `'overwrite'` does, but only where the archive's copy
 * has a more recent `updated_at`. A tie keeps the target — equal timestamps
 * say nothing about which side to prefer — as does a missing or unparseable
 * timestamp on either side, which is reported as a job warning. Across two
 * installs this is only as reliable as the two machines' clocks: UTC removes
 * timezone error, not skew.
 */
type ClashPolicy = 'skip' | 'overwrite' | 'newer';
/**
 * Persistent snapshot of a backup or restore job. Returned by every backup
 * REST endpoint that hands back a job (start, get, list).
 */
interface BackupJobSnapshot {
    job_id: string;
    kind: BackupJobKind;
    namespace: string;
    status: BackupJobStatus;
    phase: string | null;
    percent: number | null;
    message: string | null;
    error: string | null;
    created_at: string;
    started_at: string | null;
    completed_at: string | null;
    archive_size: number | null;
    options: Record<string, unknown>;
    warnings?: string[];
    /** Present on `validate` jobs once they complete. */
    result?: NamespaceIntegrityResult | null;
    /**
     * Validation jobs a completed restore started, one per namespace it wrote.
     * The restore does not wait for them — its data is committed either way.
     */
    validation_job_ids?: string[];
    created_by: string;
}
/**
 * A single referential or identity problem found in a namespace.
 *
 * Distinct from the reporting layer's `IntegrityIssue`, which describes a
 * PostgreSQL-side finding keyed on `entity_id`. This one is a MongoDB-side
 * finding about a specific document version.
 */
interface NamespaceIntegrityIssue {
    type: string;
    severity: 'error' | 'warning' | 'info';
    document_id: string;
    template_id: string;
    version: number;
    field_path: string | null;
    reference: string;
    message: string;
}
interface NamespaceIntegritySummary {
    total_documents: number;
    documents_checked: number;
    documents_with_issues: number;
    orphaned_template_refs: number;
    orphaned_term_refs: number;
    inactive_template_refs: number;
    orphaned_document_refs: number;
    orphaned_file_refs: number;
    identity_hash_mismatches: number;
}
/**
 * The outcome of a namespace validation job.
 *
 * Findings do not fail the job — the check ran, and its answer is the
 * deliverable. `issues` is a capped sample; `issues_truncated` says how many
 * more there were, since a namespace with a systematic problem produces one
 * issue per document.
 */
interface NamespaceIntegrityResult {
    status: 'healthy' | 'warning' | 'error';
    summary: NamespaceIntegritySummary;
    issues: NamespaceIntegrityIssue[];
    issues_truncated: number;
}
/** Query parameters for `POST /backup/namespaces/{namespace}/validate`. */
interface ValidateNamespaceParams {
    /** Check term references (one cached lookup per distinct term). */
    check_term_refs?: boolean;
    /** Recompute each document's identity hash and compare it to the stored one. */
    check_identity?: boolean;
    /** Stop after this many documents (0 = all). */
    limit?: number;
}
/**
 * Request body for `POST /backup/namespaces/{namespace}/backup`.
 *
 * Only the options the direct backup engine consumes are typed — the
 * endpoint rejects the retired fields (`skip_closure`, `skip_synonyms`,
 * `latest_only`, `template_prefixes`, `dry_run`, `include_inactive`) with a
 * 400 when set. Backups always contain every entity in every status:
 * inactive and archived entities are referenced by live data, so an archive
 * missing them would be a restore trap. Blob bytes stream to the server's
 * backup scratch dir, so `include_files: true` is safe at any content
 * volume.
 */
interface BackupRequest {
    include_files?: boolean;
    skip_documents?: boolean;
    namespaces?: string[];
    all_namespaces?: boolean;
}
/**
 * Form fields accompanying a multipart restore upload.
 *
 * `'restore'` and `'merge'` are ID-preserving and write each namespace in
 * the archive back to itself (a merge may target a differently-named
 * namespace). `'fresh'` re-mints every identity: it takes
 * `target_namespace` (single-namespace archive) or `namespace_map`
 * (multi-namespace). `dry_run` is real, and for a merge it is exact: the
 * plan is computed before anything is written, so the report is what a real
 * run would do — and it still fails on what a real run would refuse. The
 * retired toolkit-era params are gone from this type: `register_synonyms`
 * was removed from the API entirely (fresh means fresh — no old→new id
 * back-ties), and `continue_on_error` is a tombstone the endpoint 400s
 * when set.
 *
 * The clash policies apply to `mode: 'merge'` only. Sending a non-default
 * one with a plain restore is a 400 rather than a silent no-op: a restore
 * requires an empty target, so nothing can clash, and quietly accepting the
 * option would misreport what ran.
 */
interface RestoreOptions {
    mode?: RestoreMode;
    /**
     * Where to write. Required for `mode: 'fresh'` on a single-namespace
     * archive, which is placing new identities somewhere. For the other modes
     * the archive manifest decides, except that a merge may use it to write
     * into a differently-named namespace.
     */
    target_namespace?: string;
    /**
     * Fresh only — explicit `{source: target}` for EVERY namespace in a
     * multi-namespace archive; there is no implicit default, because an
     * unmapped namespace restored to its old name would collide with the live
     * original. Several sources may share one target (Registry-key collisions
     * between them refuse at plan time); a target may equal its source name
     * only when that namespace is absent. Pass exactly one of this or
     * `target_namespace` for `'fresh'`.
     */
    namespace_map?: Record<string, string>;
    /** Merge only — resolution for a document identity the target already holds. */
    on_clash?: ClashPolicy;
    /**
     * Merge only — insert terminologies and templates the target does not
     * have. Without it a missing definition refuses the merge: changing a live
     * namespace's definitions is an active decision, not a side effect of
     * restoring data into it.
     */
    add_missing?: boolean;
    /** Merge only — add terms the target's terminology is missing. */
    extend_terminologies?: boolean;
    skip_documents?: boolean;
    skip_files?: boolean;
    batch_size?: number;
    dry_run?: boolean;
    /**
     * Drop a stale reporting schema for the target namespace before
     * restoring instead of refusing. A dry run reports the would-drop only.
     * Rejected for a merge: its target is live, so a populated reporting
     * schema is expected and dropping it would discard the data being merged
     * into.
     */
    drop_stale_reporting?: boolean;
}
/**
 * Filter parameters for `GET /backup/jobs`.
 */
interface ListBackupJobsParams {
    namespace?: string;
    status?: BackupJobStatus;
    limit?: number;
}
/**
 * SSE wire envelope yielded by `streamBackupJobEvents`.
 *
 * Mirrors `BackupProgressMessage` on the server. `phase` is intentionally a
 * free-form string — it is a runtime convention shared between producer and
 * consumer, not a schema contract. Phase names may change between toolkit
 * versions; treat them as opaque strings for display/log purposes.
 */
interface BackupProgressMessage {
    job_id: string;
    status: BackupJobStatus;
    phase: string | null;
    percent: number | null;
    message: string | null;
    current: number | null;
    total: number | null;
    details: Record<string, unknown> | null;
}

declare class DocumentStoreService extends BaseService {
    constructor(transport: FetchTransport);
    listDocuments(params?: DocumentQueryParams): Promise<DocumentListResponse>;
    /**
     * Which templates are a namespace's documents instances of?
     *
     * Grouped from the documents themselves, not from template ownership: a
     * document's namespace is independent of its template's namespace, so a
     * template picker built from the namespace's OWN templates misses shared
     * and foreign templates its documents actually use. Counts are distinct
     * logical documents (version rows collapse before counting); each facet
     * carries the template's own namespace so cross-namespace entries can be
     * labeled honestly.
     */
    getTemplateFacets(params?: TemplateFacetsParams): Promise<TemplateFacetsResponse>;
    /**
     * Fetch a document by ID (or any synonym/value the Registry resolves).
     *
     * `namespace` (CASE-457): under a MULTI-namespace key (e.g. the install admin
     * key), a value-form `id` has no namespace context to resolve against — pass
     * `namespace` to scope it. Maps to the `?namespace=` query param the endpoint
     * accepts. Single-namespace keys derive it automatically and can omit it.
     */
    getDocument(id: string, version?: number, namespace?: string): Promise<Document>;
    createDocument(data: CreateDocumentRequest): Promise<BulkResultItem>;
    createDocuments(data: CreateDocumentRequest[]): Promise<BulkResponse>;
    /**
     * Apply an RFC 7396 JSON Merge Patch to a document.
     *
     * Single-item convenience that wraps the bulk PATCH endpoint and unwraps the
     * single result. Throws {@link WipBulkItemError} (with `errorCode` populated)
     * on a per-item failure (e.g. `not_found`, `validation_failed`,
     * `concurrency_conflict`, `identity_field_change`).
     *
     * Identity fields cannot be changed via PATCH — use {@link createDocument}
     * to create a new document instead.
     */
    updateDocument(documentId: string, patch: Record<string, unknown>, options?: {
        ifMatch?: number;
        metadataPatch?: Record<string, unknown>;
    }): Promise<BulkResultItem>;
    /**
     * Bulk PATCH /documents — apply RFC 7396 merge patches to multiple documents
     * in a single round-trip. Each item is processed independently; per-item
     * failures appear in the response with a populated `error_code`.
     */
    updateDocuments(items: PatchDocumentRequest[]): Promise<BulkResponse>;
    deleteDocument(id: string, options?: {
        updatedBy?: string;
        hardDelete?: boolean;
        version?: number;
    }): Promise<BulkResultItem>;
    deleteDocuments(ids: string[], options?: {
        hardDelete?: boolean;
    }): Promise<BulkResponse>;
    archiveDocument(id: string, archivedBy?: string): Promise<BulkResultItem>;
    validateDocument(data: ValidateDocumentRequest): Promise<DocumentValidationResponse>;
    /**
     * Bulk validate (CASE-419): validate many data payloads against ONE template
     * without saving. Side-effect-free — no documents/versions/identity-hash
     * registrations. Returns per-item results in input order.
     */
    validateDocuments(request: ValidateDocumentsRequest): Promise<BulkValidationResponse>;
    getVersions(id: string): Promise<DocumentVersionResponse>;
    getVersion(id: string, version: number): Promise<Document>;
    getTableView(templateId: string, params?: TableViewParams): Promise<TableViewResponse>;
    exportTableCsv(templateId: string, params?: {
        status?: string;
        include_metadata?: boolean;
        max_cross_product?: number;
    }): Promise<Blob>;
    getLatestDocument(id: string): Promise<Document>;
    getDocumentByIdentity(identityHash: string, includeInactive?: boolean, namespace?: string): Promise<Document>;
    /**
     * Query documents by template + filters (POST /documents/query).
     *
     * `namespace` (CASE-457): the read fails SILENTLY (total: 0, no error pre-fix)
     * when a value-form `template_id`/`template_value` can't resolve for lack of
     * namespace context — i.e. a MULTI-namespace key (the install admin key) with
     * no scope. Pass `namespace` to supply it. It maps to the `?namespace=` QUERY
     * PARAM, NOT the body — `namespace` in the JSON body is rejected
     * `extra_forbidden` (StrictModel). Single-namespace keys derive it and can omit.
     */
    queryDocuments(body: DocumentQueryRequest, namespace?: string): Promise<DocumentListResponse>;
    /**
     * List relationship documents touching a document.
     *
     * Returns relationship documents (templates with `usage: 'relationship'`)
     * that point at (incoming) or from (outgoing) the given document.
     *
     * Backed by Mongo indexes on `(template_id, data.source_ref)` and
     * `(template_id, data.target_ref)` — query is O(matches), not
     * O(documents).
     *
     * @param documentId Seed document ID (or any synonym/value the Registry resolves).
     * @param params Filter, pagination, and namespace overrides.
     */
    getDocumentRelationships(documentId: string, params?: DocumentRelationshipsParams): Promise<DocumentListResponse>;
    /**
     * BFS traversal through relationship documents from a seed document.
     *
     * Capped at `depth=10` and `max_nodes=1000` (safety bounds). When a
     * cap fires, the response sets `truncated: true`.
     *
     * @param documentId Seed document ID.
     * @param params Depth (1..10), type filter, direction, namespace.
     */
    traverseDocuments(documentId: string, params?: DocumentTraverseParams): Promise<DocumentTraverseResponse>;
    /**
     * Migrate a cohort of documents from one template version to another —
     * a validated, identity-preserving bulk re-pin.
     *
     * `dry_run` defaults to true on the server: run it first and check
     * `failed === 0` before applying. Bulk-first: always HTTP 200,
     * per-document outcome in `results`. Operation-level problems (bad
     * versions, identity-fields mismatch, inactive target) throw as 4xx.
     *
     * @param request Template (UUID or value/synonym), from/to versions, dry_run.
     * @param namespace Cohort namespace. Omittable only for single-namespace keys.
     */
    migrateDocuments(request: DocumentMigrateRequest, namespace?: string): Promise<DocumentMigrateResponse>;
    previewImport(file: Blob, filename: string): Promise<ImportPreviewResponse>;
    importDocuments(file: Blob, filename: string, options: ImportDocumentsOptions): Promise<ImportDocumentsResponse>;
    startReplay(request?: ReplayRequest): Promise<ReplaySessionResponse>;
    getReplayStatus(sessionId: string): Promise<ReplaySessionResponse>;
    pauseReplay(sessionId: string): Promise<ReplaySessionResponse>;
    resumeReplay(sessionId: string): Promise<ReplaySessionResponse>;
    cancelReplay(sessionId: string): Promise<ReplaySessionResponse>;
    /**
     * Start a namespace backup. Returns the initial job snapshot (status
     * `pending` or `running`).
     *
     * Pass an empty object to take all defaults. Note `include_files`
     * defaults to `false` — see CASE-28 before setting it to `true`.
     */
    startBackup(namespace: string, request?: BackupRequest): Promise<BackupJobSnapshot>;
    /**
     * Restore from an uploaded archive. The archive is streamed to disk on
     * the server, so multi-GB uploads do not buffer in memory.
     *
     * ID-preserving restore-to-self: the archive manifest determines the
     * target namespaces (each writes to itself). `mode: 'restore'` (default)
     * requires every target to be empty; `mode: 'merge'` reconciles the
     * archive into a namespace that already holds data, under the
     * definitions-compatibility check, then `on_clash` for documents. Set `dry_run: true` to get the
     * report without writing anything. Retired toolkit-era params are no
     * longer sent — the endpoint 400s them; see `RestoreOptions`.
     */
    startRestore(namespace: string, archive: Blob | File, options?: RestoreOptions, filename?: string): Promise<BackupJobSnapshot>;
    /**
     * Verify a namespace's referential and identity integrity. Returns a job.
     *
     * Checks that every reference resolves — template, term, document, file —
     * and that every document's stored identity hash still matches its own
     * data. It is the referential twin of the reporting parity check: that one
     * compares PostgreSQL against MongoDB, this compares MongoDB against
     * itself.
     *
     * It matters most after a restore, which writes documents straight to
     * MongoDB and validates nothing while writing. Every restore starts one of
     * these per namespace it wrote and records the ids on its own snapshot
     * (`validation_job_ids`); this is the same check on demand.
     *
     * Poll `getBackupJob` for progress and `result`. Findings do not fail the
     * job — it completes with `result.status` of healthy, warning or error.
     */
    validateNamespace(namespace: string, params?: ValidateNamespaceParams): Promise<BackupJobSnapshot>;
    /** Get the latest persisted snapshot for a backup or restore job. */
    getBackupJob(jobId: string): Promise<BackupJobSnapshot>;
    /** List recent backup/restore jobs, optionally filtered. */
    listBackupJobs(params?: ListBackupJobsParams): Promise<BackupJobSnapshot[]>;
    /**
     * Download the archive produced by a completed backup job.
     *
     * Throws `WipConflictError` (409) if the job is not yet complete,
     * `WipNotFoundError` (404) if the job_id is unknown, or `WipError` (410)
     * if the archive file has already been cleaned up from disk.
     */
    downloadBackupArchive(jobId: string): Promise<Blob>;
    /** Delete a backup/restore job and its archive file. */
    deleteBackupJob(jobId: string): Promise<void>;
    /**
     * Async iterator over Server-Sent Events for a backup/restore job.
     *
     * Yields a `BackupProgressMessage` for every change in the job's status,
     * phase, percent or message. Terminates when the job reaches a terminal
     * state (`complete` or `failed`) or when the consumer aborts via
     * `signal`.
     *
     * Example:
     * ```ts
     * const ctrl = new AbortController()
     * for await (const evt of client.documents.streamBackupJobEvents(jobId, ctrl.signal)) {
     *   console.log(evt.phase, evt.percent, evt.message)
     *   if (evt.status === 'complete' || evt.status === 'failed') break
     * }
     * ```
     */
    streamBackupJobEvents(jobId: string, signal?: AbortSignal): AsyncIterableIterator<BackupProgressMessage>;
}

type FileStatus = 'orphan' | 'active' | 'inactive';
interface FileMetadata {
    description: string | null;
    tags: string[];
    category: string | null;
    custom: Record<string, unknown>;
}
interface FileEntity {
    file_id: string;
    namespace: string;
    filename: string;
    content_type: string;
    size_bytes: number;
    checksum: string;
    storage_key: string;
    metadata: FileMetadata;
    status: FileStatus;
    reference_count: number;
    allowed_templates: string[] | null;
    uploaded_at: string;
    uploaded_by: string | null;
    updated_at: string | null;
    updated_by: string | null;
}
interface FileUploadMetadata {
    description?: string;
    tags?: string[];
    category?: string;
    custom?: Record<string, unknown>;
    allowed_templates?: string[];
}
interface UpdateFileMetadataRequest {
    description?: string;
    tags?: string[];
    category?: string;
    custom?: Record<string, unknown>;
    allowed_templates?: string[];
}
type FileListResponse = PaginatedResponse<FileEntity>;
interface FileDownloadResponse {
    file_id: string;
    filename: string;
    content_type: string;
    size_bytes: number;
    download_url: string;
    expires_in: number;
}
interface FileIntegrityIssue {
    type: 'orphan_file' | 'missing_storage' | 'broken_reference';
    severity: 'warning' | 'error';
    file_id: string | null;
    document_id: string | null;
    field_path: string | null;
    message: string;
}
interface FileIntegrityResponse {
    status: 'healthy' | 'warning' | 'error';
    checked_at: string;
    summary: Record<string, number>;
    issues: FileIntegrityIssue[];
}
interface FileQueryParams {
    namespace?: string;
    status?: FileStatus;
    content_type?: string;
    category?: string;
    tags?: string;
    uploaded_by?: string;
    page?: number;
    page_size?: number;
}

declare class FileStoreService extends BaseService {
    constructor(transport: FetchTransport);
    uploadFile(file: File | Blob, filename?: string, metadata?: FileUploadMetadata, namespace?: string): Promise<FileEntity>;
    listFiles(params?: FileQueryParams): Promise<FileListResponse>;
    getFile(fileId: string): Promise<FileEntity>;
    getDownloadUrl(fileId: string, expiresIn?: number): Promise<FileDownloadResponse>;
    downloadFileContent(fileId: string): Promise<Blob>;
    updateMetadata(fileId: string, data: UpdateFileMetadataRequest): Promise<BulkResultItem>;
    deleteFile(fileId: string): Promise<BulkResultItem>;
    deleteFiles(fileIds: string[]): Promise<BulkResponse>;
    hardDeleteFile(fileId: string): Promise<void>;
    listOrphans(params?: {
        older_than_hours?: number;
        limit?: number;
    }): Promise<FileEntity[]>;
    findByChecksum(checksum: string): Promise<FileEntity[]>;
    checkIntegrity(): Promise<FileIntegrityResponse>;
    getFileDocuments(fileId: string, page?: number, pageSize?: number): Promise<{
        items: Array<{
            document_id: string;
            template_id: string;
            template_value: string | null;
            field_path: string;
            status: string;
            created_at: string | null;
        }>;
        total: number;
        page: number;
        page_size: number;
        pages: number;
    }>;
}

interface Namespace {
    prefix: string;
    description: string;
    isolation_mode: 'open' | 'strict';
    deletion_mode: 'retain' | 'full';
    allowed_external_refs: string[];
    id_config: Record<string, IdAlgorithmConfig>;
    status: 'active' | 'archived' | 'deleted';
    created_at: string;
    created_by: string | null;
    updated_at: string;
    updated_by: string | null;
}
interface NamespaceStats {
    prefix: string;
    description: string;
    isolation_mode: string;
    deletion_mode: string;
    status: string;
    entity_counts: Record<string, number>;
}
interface IdAlgorithmConfig {
    algorithm: 'uuid7' | 'uuid4' | 'prefixed' | 'nanoid' | 'pattern' | 'any';
    prefix?: string;
    pad?: number;
    length?: number;
    pattern?: string;
}
interface CreateNamespaceRequest {
    prefix: string;
    description?: string;
    isolation_mode?: 'open' | 'strict';
    deletion_mode?: 'retain' | 'full';
    allowed_external_refs?: string[];
    id_config?: Record<string, IdAlgorithmConfig>;
    created_by?: string;
}
interface UpdateNamespaceRequest {
    description?: string;
    isolation_mode?: 'open' | 'strict';
    deletion_mode?: 'retain' | 'full';
    allowed_external_refs?: string[];
    id_config?: Record<string, IdAlgorithmConfig>;
    updated_by?: string;
    /**
     * Required to be `true` when flipping `deletion_mode` from 'retain' to
     * 'full' on an existing namespace (enabling hard-delete on namespace
     * deletion). The backend rejects the transition without it. Ignored for
     * other updates, and not needed when creating a namespace directly with
     * `deletion_mode: 'full'`. Safety guard — see CASE-291 / CASE-429.
     */
    confirm_enable_deletion?: boolean;
}
interface RegistryEntry {
    entry_id: string;
    namespace: string;
    entity_type: string;
    primary_composite_key: Record<string, unknown>;
    synonyms_count: number;
    status: 'active' | 'reserved' | 'inactive';
    created_at: string;
    created_by: string | null;
    updated_at: string;
}
type RegistryEntryListResponse = PaginatedResponse<RegistryEntry>;
interface RegistrySourceInfo {
    system_id: string;
    endpoint_url: string | null;
}
interface RegistrySynonym {
    namespace: string;
    entity_type: string;
    composite_key: Record<string, unknown>;
    composite_key_hash: string;
    source_info: RegistrySourceInfo | null;
    created_at: string;
    created_by: string | null;
}
interface RegistryEntryFull {
    entry_id: string;
    namespace: string;
    entity_type: string;
    primary_composite_key: Record<string, unknown>;
    primary_composite_key_hash: string;
    synonyms: RegistrySynonym[];
    source_info: RegistrySourceInfo | null;
    search_values: string[];
    metadata: Record<string, unknown>;
    status: string;
    created_at: string;
    created_by: string | null;
    updated_at: string;
    updated_by: string | null;
}
/**
 * A single hit from POST /api/registry/search/by-term (CASE-572).
 *
 * Distinct from RegistryLookupResponse: the by-term route returns
 * `registry_id`/`matched_in`, not `entry_id`/`matched_via`.
 */
interface RegistryByTermHit {
    registry_id: string;
    namespace: string;
    entity_type: string;
    matched_in: 'primary' | 'synonym';
    matched_namespace: string;
    matched_entity_type: string;
    matched_composite_key: Record<string, unknown>;
    all_synonyms: RegistrySynonym[];
}
interface RegistryLookupResponse {
    index: number;
    status: string;
    entry_id: string | null;
    namespace: string | null;
    entity_type: string | null;
    matched_namespace: string | null;
    matched_entity_type: string | null;
    matched_composite_key: Record<string, unknown> | null;
    matched_via: string | null;
    synonyms: RegistrySynonym[];
    source_info: RegistrySourceInfo | null;
    source_data: Record<string, unknown> | null;
    error: string | null;
}
interface RegistryBrowseParams {
    namespace?: string;
    entity_type?: string;
    status?: string;
    q?: string;
    page?: number;
    page_size?: number;
}
interface RegistrySearchResult {
    entry_id: string;
    namespace: string;
    entity_type: string;
    status: string;
    primary_composite_key: Record<string, unknown>;
    synonyms: RegistrySynonym[];
    source_info: RegistrySourceInfo | null;
    metadata: Record<string, unknown>;
    created_at: string;
    created_by: string | null;
    updated_at: string;
    updated_by: string | null;
    matched_via: 'entry_id' | 'composite_key_value' | 'synonym_key_value';
    matched_value: string;
    resolution_path: string;
}
interface RegistrySearchResponse {
    items: RegistrySearchResult[];
    total: number;
    page: number;
    page_size: number;
    query: string;
}
interface RegistrySearchParams {
    q: string;
    namespace?: string;
    entity_type?: string;
    status?: string;
    page?: number;
    page_size?: number;
}
interface AddSynonymRequest {
    target_id: string;
    synonym_namespace: string;
    synonym_entity_type: string;
    synonym_composite_key: Record<string, unknown>;
    synonym_source_info?: {
        system_id: string;
        endpoint_url?: string;
    };
    created_by?: string;
}
interface RemoveSynonymRequest {
    target_id: string;
    synonym_namespace: string;
    synonym_entity_type: string;
    synonym_composite_key: Record<string, unknown>;
    updated_by?: string;
}
interface MergeRequest {
    preferred_id: string;
    deprecated_id: string;
    updated_by?: string;
}
interface ExportResponse {
    export_id: string;
    prefix: string;
    download_url: string;
    stats: Record<string, number>;
}
interface ImportResponse {
    prefix: string;
    mode: 'create' | 'merge' | 'replace';
    stats: Record<string, number>;
    source_prefix: string | null;
}
type GrantSubjectType = 'user' | 'api_key' | 'group';
type GrantPermission = 'read' | 'write' | 'admin';
interface Grant {
    namespace: string;
    subject: string;
    subject_type: GrantSubjectType;
    permission: GrantPermission;
    granted_by: string;
    granted_at: string;
    expires_at: string | null;
}
interface CreateGrantRequest {
    subject: string;
    subject_type?: GrantSubjectType;
    permission?: GrantPermission;
    expires_at?: string;
}
interface RevokeGrantRequest {
    subject: string;
    subject_type?: GrantSubjectType;
}
interface GrantBulkResult {
    index: number;
    status: 'created' | 'updated' | 'error';
    subject: string;
    permission: string | null;
    error: string | null;
}
interface GrantBulkResponse {
    results: GrantBulkResult[];
    total: number;
    succeeded: number;
    failed: number;
}
interface GrantRevokeResult {
    index: number;
    status: 'revoked' | 'not_found';
    subject: string;
}
interface GrantRevokeBulkResponse {
    results: GrantRevokeResult[];
    total: number;
    succeeded: number;
    failed: number;
}
interface APIKeyInfo {
    name: string;
    owner: string;
    groups: string[];
    description: string | null;
    created_at: string;
    expires_at: string | null;
    enabled: boolean;
    namespaces: string[] | null;
    created_by: string;
    source: 'config' | 'runtime';
    /**
     * Config-declared namespace grants ({namespace: read|write|admin}).
     * Always null for runtime keys — their write authority is Registry
     * NamespaceGrants, not shown here (CASE-693).
     */
    grants: Record<string, 'read' | 'write' | 'admin'> | null;
}
interface CreateAPIKeyRequest {
    name: string;
    owner?: string;
    groups?: string[];
    namespaces?: string[] | null;
    description?: string;
    expires_at?: string;
    /**
     * CASE-450: also create a namespace grant at this level for the new key
     * (subject = key name) on each namespace in `namespaces`. Without it a
     * scoped key can read its namespaces but not write.
     */
    grant_permission?: 'read' | 'write' | 'admin';
}
interface CreateAPIKeyResponse extends APIKeyInfo {
    plaintext_key: string;
    /** Namespaces a grant was created on (CASE-450 grant_permission). */
    granted_namespaces?: string[] | null;
}
interface UpdateAPIKeyRequest {
    description?: string;
    groups?: string[];
    namespaces?: string[] | null;
    expires_at?: string;
    enabled?: boolean;
}
/**
 * Paginated list response from `GET /api/registry/api-keys` (CASE-335).
 * Follows the platform-wide pagination envelope (see `wip://conventions`).
 */
type APIKeyListResponse = PaginatedResponse<APIKeyInfo>;
/** Query params for `GET /api/registry/api-keys` (CASE-335). */
interface ListAPIKeysParams {
    /** Default 1. */
    page?: number;
    /** Default 50, capped at 100. */
    page_size?: number;
}

declare class RegistryService extends BaseService {
    constructor(transport: FetchTransport);
    listNamespaces(includeArchived?: boolean): Promise<Namespace[]>;
    getNamespace(prefix: string): Promise<Namespace>;
    getNamespaceStats(prefix: string): Promise<NamespaceStats>;
    createNamespace(data: CreateNamespaceRequest): Promise<Namespace>;
    updateNamespace(prefix: string, data: UpdateNamespaceRequest): Promise<Namespace>;
    /**
     * Upsert a namespace — create if missing, update if existing.
     *
     * Equivalent to `updateNamespace` but communicates intent: callers
     * (typically app bootstrap scripts) want a single self-healing call
     * that succeeds whether the namespace already exists or not. On
     * create, any field not supplied uses the platform default
     * (isolation_mode='open', deletion_mode='retain', etc).
     */
    upsertNamespace(prefix: string, data: UpdateNamespaceRequest): Promise<Namespace>;
    archiveNamespace(prefix: string, archivedBy?: string): Promise<Namespace>;
    restoreNamespace(prefix: string, restoredBy?: string): Promise<Namespace>;
    deleteNamespace(prefix: string, deletedBy?: string): Promise<void>;
    initializeWipNamespace(): Promise<Namespace>;
    listEntries(params?: RegistryBrowseParams): Promise<RegistryEntryListResponse>;
    lookupEntry(entryId: string): Promise<RegistryLookupResponse>;
    /**
     * Free-text search across composite key values (CASE-572, breaking in 0.28.0).
     *
     * Returns `{ hits, total }`: `total` is the full server-side match count
     * even when `limit` bounds the returned hits. `limit` requires a backend
     * that accepts it (registry rejects unknown fields with 422 — ships
     * together with this client change).
     */
    searchEntries(term: string, options?: {
        namespaces?: string[];
        entityTypes?: string[];
        includeInactive?: boolean;
        limit?: number;
    }): Promise<{
        hits: RegistryByTermHit[];
        total: number;
    }>;
    unifiedSearch(params: RegistrySearchParams): Promise<RegistrySearchResponse>;
    getEntry(entryId: string): Promise<RegistryEntryFull>;
    addSynonym(request: AddSynonymRequest): Promise<{
        status: string;
        registry_id?: string;
        error?: string;
    }>;
    removeSynonym(request: RemoveSynonymRequest): Promise<{
        status: string;
        registry_id?: string;
        error?: string;
    }>;
    mergeEntries(request: MergeRequest): Promise<{
        status: string;
        preferred_id?: string;
        deprecated_id?: string;
        error?: string;
    }>;
    deactivateEntry(entryId: string, updatedBy?: string): Promise<{
        status: string;
    }>;
    exportNamespace(prefix: string, options?: {
        include_files?: boolean;
    }): Promise<ExportResponse>;
    downloadExport(exportId: string): Promise<Blob>;
    importNamespace(file: Blob, options?: {
        target_prefix?: string;
        mode?: 'create' | 'merge' | 'replace';
        imported_by?: string;
    }): Promise<ImportResponse>;
    listGrants(prefix: string): Promise<Grant[]>;
    createGrants(prefix: string, grants: CreateGrantRequest[]): Promise<GrantBulkResponse>;
    revokeGrants(prefix: string, grants: RevokeGrantRequest[]): Promise<GrantRevokeBulkResponse>;
    /**
     * List API keys with pagination (CASE-335).
     *
     * Breaking change in @wip/client 0.19.0: the response shape is now a
     * `PaginatedResponse<APIKeyInfo>` (envelope with `items`/`total`/`page`/
     * `page_size`/`pages`) instead of a bare `APIKeyInfo[]`. Callers using
     * `.map(...)` on the result must switch to `.items.map(...)`.
     */
    listAPIKeys(params?: ListAPIKeysParams): Promise<APIKeyListResponse>;
    createAPIKey(request: CreateAPIKeyRequest): Promise<CreateAPIKeyResponse>;
    getAPIKey(name: string): Promise<APIKeyInfo>;
    updateAPIKey(name: string, request: UpdateAPIKeyRequest): Promise<APIKeyInfo>;
    revokeAPIKey(name: string): Promise<{
        status: string;
        name: string;
    }>;
}

interface ReportQueryParams {
    /** SQL SELECT query (write operations forbidden) */
    sql: string;
    /** Positional parameters ($1, $2, ...) */
    params?: unknown[];
    /** Query timeout in seconds (1-300, default 30) */
    timeout_seconds?: number;
    /** Max rows returned (1-50000, default 1000) */
    max_rows?: number;
    /**
     * Namespace whose PostgreSQL schema unqualified table names resolve in.
     * Each namespace is its own schema (a table is `"<ns>"."doc_<value>"`);
     * when set, the server runs the query with search_path pointed there, so
     * `doc_<value>` works unqualified. Omit for cross-namespace queries and
     * schema-qualify each table in the SQL instead.
     */
    namespace?: string;
}
interface ReportQueryResult {
    columns: string[];
    rows: unknown[][];
    row_count: number;
    truncated: boolean;
}
interface ReportTableColumn {
    name: string;
    type: string;
    nullable: boolean;
}
/**
 * One reporting relation as `/tables` lists it. Post per-version split,
 * a template ("entity") owns several relations: physical per-version
 * tables `doc_<value>__v<N>`, the identity-core view
 * `doc_<value>__entities`, and the bare-name view `doc_<value>` — the
 * default query surface. Never sum an entity's sibling relations (they
 * overlap by construction); use `ReportEntity.row_count` instead.
 */
interface ReportTable {
    namespace: string;
    name: string;
    /** 'view' (entity views) or 'table' (physical / legacy pre-split). */
    kind: 'view' | 'table';
    /** Stripped doc_* stem for document relations, null for metadata tables. */
    template_value: string | null;
    qualified_name: string;
    row_count: number;
    /** Summary mode only. */
    column_count?: number;
    /** Detail mode (listTables with a tableName) only. */
    columns?: ReportTableColumn[];
}
interface ReportEntityVersion {
    version: number;
    table: string;
    row_count: number;
}
/**
 * Entity-first grouping from `/tables` — one entry per template with its
 * version tables and views. `row_count` is the entity's document count
 * (a document lives in exactly one version table under latest_only).
 * `legacy_table: true` = a pre-split physical table still occupies the
 * bare name (needs an explicit drop + batch-sync rebuild — the platform
 * never auto-drops it).
 */
interface ReportEntity {
    namespace: string;
    entity: string;
    default_view: string;
    default_view_present: boolean;
    entities_view: string;
    legacy_table: boolean;
    versions: ReportEntityVersion[];
    row_count: number;
}
interface ReportTableSchema {
    namespace: string;
    template_value: string;
    schema: string;
    table_name: string;
    qualified_name: string;
    columns: ReportTableColumn[];
    row_count: number;
}
interface SyncStatus {
    running: boolean;
    connected_to_nats: boolean;
    connected_to_postgres: boolean;
    last_event_processed: string | null;
    events_processed: number;
    events_failed: number;
    tables_managed: number;
}
interface HealthResponse {
    status: 'healthy' | 'degraded' | 'unhealthy';
    service: string;
    version: string;
    nats_connected: boolean;
    postgres_connected: boolean;
    details: Record<string, unknown>;
}
interface PerTemplateStats {
    template_value: string;
    table_name: string;
    documents_synced: number;
    documents_failed: number;
    last_sync_at: string | null;
    last_error: string | null;
    last_error_at: string | null;
}
interface ConsumerInfo {
    stream_name: string;
    consumer_name: string;
    pending_messages: number;
    pending_bytes: number;
    delivered_messages: number;
    ack_pending: number;
    redelivered: number;
    last_delivered: string | null;
}
interface LatencyStats {
    sample_count: number;
    min_ms: number;
    max_ms: number;
    avg_ms: number;
    p50_ms: number;
    p95_ms: number;
    p99_ms: number;
}
interface MetricsResponse {
    started_at: string;
    uptime_seconds: number;
    nats_connected: boolean;
    postgres_connected: boolean;
    events_processed: number;
    events_failed: number;
    events_per_second: number;
    consumer_info: ConsumerInfo | null;
    processing_latency: LatencyStats;
    template_stats: PerTemplateStats[];
    errors_by_type: Record<string, number>;
}
type AlertSeverity = 'info' | 'warning' | 'critical';
type AlertType = 'queue_lag' | 'error_rate' | 'processing_stalled' | 'connection_lost';
interface Alert {
    alert_id: string;
    alert_type: AlertType;
    severity: AlertSeverity;
    message: string;
    triggered_at: string;
    resolved_at: string | null;
    details: Record<string, unknown>;
}
interface AlertThresholds {
    queue_lag_warning: number;
    queue_lag_critical: number;
    error_rate_warning: number;
    error_rate_critical: number;
    stall_warning_seconds: number;
    stall_critical_seconds: number;
}
interface AlertConfig {
    enabled: boolean;
    check_interval_seconds: number;
    thresholds: AlertThresholds;
    webhook_url: string | null;
    webhook_headers: Record<string, string>;
}
interface AlertsResponse {
    config: AlertConfig;
    active_alerts: Alert[];
    resolved_alerts: Alert[];
}
type BatchSyncStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';
interface BatchSyncRequest {
    template_value?: string;
    force?: boolean;
    page_size?: number;
}
interface BatchSyncJob {
    job_id: string;
    template_value: string;
    /** Resolved canonical template id — a value alone is ambiguous when
     * several namespaces share it. */
    template_id?: string | null;
    /** Document scope: null = all namespaces, set = only that namespace. */
    namespace?: string | null;
    status: BatchSyncStatus;
    started_at: string | null;
    completed_at: string | null;
    total_documents: number;
    documents_synced: number;
    documents_failed: number;
    current_page: number;
    error_message: string | null;
}
interface BatchSyncResponse {
    job_id: string;
    template_value: string;
    /** Document scope the job was started with (null = all namespaces). */
    namespace?: string | null;
    status: BatchSyncStatus;
    message: string;
}
/**
 * Result shape for the entity-table batch syncs (terminologies, terms,
 * term_relations). Synchronous on the server (no per-job polling); the
 * full result is in the response body.
 */
interface BatchEntitySyncResult {
    status: 'completed' | 'failed';
    table: 'terminologies' | 'terms' | 'term_relations';
    /** Entries fetched from the source service. */
    fetched?: number;
    /** Entries upserted into PostgreSQL. */
    synced?: number;
    /** Entries that failed to upsert. */
    failed?: number;
    /** Free-form additional fields the server may emit. */
    [extra: string]: unknown;
}
interface BatchJobCancelResult {
    status: 'cancelled' | 'not_running';
    job_id: string;
}
interface BatchJobsCleared {
    cleared: number;
}
interface CsvExportQuery {
    sql: string;
    params?: unknown[];
    timeout_seconds?: number;
    filename?: string;
}
interface IntegrityIssue {
    type: string;
    severity: string;
    source: string;
    entity_id: string;
    entity_value: string | null;
    field_path: string | null;
    reference: string;
    message: string;
}
interface IntegritySummary {
    total_templates: number;
    total_documents: number;
    documents_checked: number;
    templates_with_issues: number;
    documents_with_issues: number;
    orphaned_terminology_refs: number;
    orphaned_template_refs: number;
    orphaned_term_refs: number;
    inactive_refs: number;
}
interface IntegrityCheckResult {
    status: 'healthy' | 'warning' | 'error' | 'partial';
    checked_at: string;
    services_checked: string[];
    services_unavailable: string[];
    summary: IntegritySummary;
    issues: IntegrityIssue[];
}
interface SearchResult {
    type: 'terminology' | 'term' | 'template' | 'document' | 'file';
    id: string;
    value: string | null;
    label: string | null;
    status: string | null;
    description: string | null;
    updated_at: string | null;
    /** ts_rank score; populated for FTS document hits only. */
    score?: number | null;
    /**
     * ts_headline excerpt; populated for FTS document hits only.
     * HTML by default with <b>...</b> around matched terms; pass
     * snippet_format='text' on the request for plain text.
     */
    snippet?: string | null;
}
/**
 * Per-type paginated bucket on `SearchResponse.results` (CASE-329).
 * Mirrors the platform-wide pagination envelope (see `wip://conventions`).
 */
interface SearchTypeResults {
    items: SearchResult[];
    total: number;
    page: number;
    page_size: number;
    pages: number;
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
interface SearchResponse {
    query: string;
    /** Echo of the document-search mode used (informational only). */
    mode?: string | null;
    /** Per-type paginated buckets. Only types the search visited appear here. */
    results: Record<string, SearchTypeResults>;
    /** Sum of per-type totals across all visited types. */
    total: number;
}
interface ActivityItem {
    type: 'terminology' | 'term' | 'template' | 'document' | 'file';
    action: 'created' | 'updated' | 'deleted' | 'deprecated';
    entity_id: string;
    entity_value: string | null;
    entity_label: string | null;
    timestamp: string;
    user: string | null;
    version: number | null;
    details: Record<string, unknown> | null;
}
interface ActivityResponse {
    activities: ActivityItem[];
    total: number;
}
interface DocumentReference {
    document_id: string;
    template_id: string;
    template_value: string | null;
    field_path: string;
    status: string;
    created_at: string | null;
}
interface TermDocumentsResponse {
    term_id: string;
    documents: DocumentReference[];
    total: number;
}
interface EntityReference {
    ref_type: 'template' | 'terminology' | 'term';
    ref_id: string;
    ref_value: string | null;
    ref_label: string | null;
    field_path: string | null;
    status: 'valid' | 'broken' | 'inactive';
    error: string | null;
}
interface EntityDetails {
    entity_type: 'document' | 'template' | 'terminology' | 'term' | 'file';
    entity_id: string;
    entity_value: string | null;
    entity_label: string | null;
    entity_status: string | null;
    version: number | null;
    created_at: string | null;
    updated_at: string | null;
    data: Record<string, unknown> | null;
    references: EntityReference[];
    valid_refs: number;
    broken_refs: number;
    inactive_refs: number;
}
interface EntityReferencesResponse {
    entity: EntityDetails | null;
    error: string | null;
}
interface IncomingReference {
    entity_type: 'document' | 'template';
    entity_id: string;
    entity_value: string | null;
    entity_label: string | null;
    entity_status: string | null;
    field_path: string | null;
    reference_type: 'uses_template' | 'extends' | 'template_ref' | 'terminology_ref' | 'term_ref' | 'file_ref';
}
interface ReferencedByResponse {
    entity_type: 'document' | 'template' | 'terminology' | 'term' | 'file';
    entity_id: string;
    entity_value: string | null;
    entity_label: string | null;
    referenced_by: IncomingReference[];
    total: number;
    error: string | null;
}

declare class ReportingSyncService extends BaseService {
    constructor(transport: FetchTransport);
    healthCheck(): Promise<boolean>;
    getSyncStatus(): Promise<SyncStatus>;
    /**
     * Execute a read-only SQL query against the PostgreSQL reporting database.
     *
     * Reporting tables live in per-namespace PostgreSQL schemas
     * (`"<ns>"."doc_<value>"`). Pass `namespace` so unqualified table names
     * resolve in that namespace's schema, or schema-qualify each table in the
     * SQL for cross-namespace queries.
     */
    runQuery(sql: string, params?: unknown[], options?: {
        timeout_seconds?: number;
        max_rows?: number;
        namespace?: string;
    }): Promise<ReportQueryResult>;
    /**
     * Trigger a batch sync for ALL templates with `sync_enabled=true`.
     * Returns one BatchSyncResponse per template; jobs run async on
     * the server. Poll `listBatchJobs()` or `getBatchJob(job_id)` for
     * progress.
     *
     * `namespace` scopes every job to that namespace's documents (the
     * template list stays instance-wide — documents may be based on
     * templates owned by other namespaces). A template whose sync is
     * already active returns its existing job instead of stacking a
     * duplicate; the trigger is idempotent and acknowledges promptly.
     *
     * `force` drops each in-scope template's existing reporting relations
     * (version tables, entity views, any legacy pre-split table) before
     * its sync — rebuild from source, the recovery path for mis-shaped
     * DDL that upserts cannot heal. Requires `namespace` (400 without
     * it). Mid-rebuild, SQL readers see relation-does-not-exist for the
     * affected templates. An already-active sync is NOT force-rebuilt —
     * its per-item message says so; cancel the job and re-trigger.
     */
    triggerBatchSyncAll(options?: {
        force?: boolean;
        page_size?: number;
        namespace?: string;
    }): Promise<BatchSyncResponse[]>;
    /**
     * Trigger a batch sync for a single template (by value).
     * Job runs async; poll `getBatchJob(job_id)` for progress.
     *
     * `namespace` disambiguates the template lookup (a value is unique
     * only within a namespace) AND scopes the sync to that namespace's
     * documents. If an overlapping sync is already active, the existing
     * job is returned instead of a duplicate.
     *
     * `force` drops the template's existing reporting relations in the
     * target namespace before syncing — rebuild from source. Requires
     * `namespace` (400 without it). If an overlapping sync is active,
     * force is NOT applied (the response message says so); cancel the
     * job and re-trigger.
     */
    triggerBatchSync(templateValue: string, options?: {
        force?: boolean;
        page_size?: number;
        namespace?: string;
    }): Promise<BatchSyncResponse>;
    /**
     * Synchronous batch sync for the terminologies entity table.
     * Returns the result inline; no per-job polling.
     */
    triggerTerminologySync(namespace: string, pageSize?: number): Promise<BatchEntitySyncResult>;
    /**
     * Synchronous batch sync for the terms entity table.
     * Iterates every active terminology in `namespace` and syncs its
     * terms.
     */
    triggerTermSync(namespace: string, pageSize?: number): Promise<BatchEntitySyncResult>;
    /**
     * Synchronous batch sync for the term_relations entity table.
     */
    triggerTermRelationSync(namespace: string, pageSize?: number): Promise<BatchEntitySyncResult>;
    /** List all batch sync jobs (in-memory, lost on reporting-sync restart). */
    listBatchJobs(): Promise<BatchSyncJob[]>;
    /** Fetch a single batch sync job by id. 404 if unknown. */
    getBatchJob(jobId: string): Promise<BatchSyncJob>;
    /** Cancel a running batch sync job. */
    cancelBatchJob(jobId: string): Promise<BatchJobCancelResult>;
    /** Clear all completed/failed/cancelled jobs from in-memory state. */
    clearCompletedJobs(): Promise<BatchJobsCleared>;
    /**
     * Wait for the reporting sync to catch up.
     *
     * Simple form — waits until at least one new event is processed:
     *   await client.reporting.awaitSync()
     *
     * Query form — waits until a specific row exists in PostgreSQL:
     *   await client.reporting.awaitSync({
     *     query: "SELECT 1 FROM dnd_monster WHERE document_id = $1",
     *     params: [docId],
     *   })
     */
    awaitSync(options?: {
        /** SQL query that should return rows when sync is complete */
        query?: string;
        /** Parameters for the SQL query */
        params?: unknown[];
        /** Timeout in milliseconds (default: 5000) */
        timeout?: number;
        /** Poll interval in milliseconds (default: 200) */
        interval?: number;
    }): Promise<void>;
    /**
     * List reporting relations, grouped entity-first. Without `tableName`,
     * the response carries `entities` (one entry per template with its
     * version tables and views — the shape UIs should render) alongside the
     * flat `tables` list. With `tableName`, returns that single relation
     * with full column detail (works for views and version tables alike),
     * and `entities` is omitted.
     */
    listTables(tableName?: string, namespace?: string): Promise<{
        tables: ReportTable[];
        entities?: ReportEntity[];
    }>;
    /**
     * Get PostgreSQL columns for a template's reporting relation.
     * `namespace` is required by the endpoint (the relation lives in that
     * namespace's schema). Without `version`: the bare-name entity view
     * (the default query surface). With `version`: that version's physical
     * table shape.
     */
    getTableSchema(templateValue: string, namespace: string, version?: number): Promise<ReportTableSchema>;
    getIntegrityCheck(params?: {
        template_status?: string;
        document_status?: string;
        template_limit?: number;
        document_limit?: number;
        check_term_refs?: boolean;
        recent_first?: boolean;
    }): Promise<IntegrityCheckResult>;
    /**
     * Unified search with per-type pagination (CASE-329).
     *
     * Breaking change in @wip/client 0.19.0: the response shape moved
     * from a flat `results: SearchResult[]` to per-type buckets keyed
     * by entity type, each with its own pagination envelope. Same
     * `page`/`page_size` applies to every type. The legacy `limit`
     * parameter is still accepted as a deprecation-window alias for
     * `page_size` — use `page_size` going forward.
     */
    search(params: {
        query: string;
        types?: string[];
        /**
         * Filter by namespace. Optional — when omitted the server runs
         * the search across all namespaces visible to the API key.
         * Single-namespace keys derive it implicitly; multi-namespace
         * keys see all of theirs.
         */
        namespace?: string;
        status?: string;
        /** Page number (1-indexed). Default 1. (CASE-329) */
        page?: number;
        /** Items per type. Default 50, cap 100. (CASE-329) */
        page_size?: number;
        /** DEPRECATED (CASE-329): alias for page_size when page=1. */
        limit?: number;
        /** Restrict document search to a single template (by value). */
        template?: string;
        /**
         * Document-search strategy. 'auto' (default) picks FTS for tables
         * with full_text_indexed fields and falls back to ILIKE elsewhere.
         * 'fts' forces FTS (skips tables without indexed fields). 'substring'
         * forces ILIKE on all tables.
         */
        mode?: 'auto' | 'fts' | 'substring';
        /**
         * When false (default), only active documents are returned —
         * aligns with PoNIF #1 "inactive means retired, not deleted".
         */
        include_inactive?: boolean;
        /**
         * Snippet rendering for FTS hits. 'html' (default) wraps matched
         * terms with <b>...</b>. 'text' returns plain text.
         */
        snippet_format?: 'html' | 'text';
    }): Promise<SearchResponse>;
    getRecentActivity(params?: {
        types?: string;
        limit?: number;
    }): Promise<ActivityResponse>;
    getTermDocuments(termId: string, limit?: number): Promise<TermDocumentsResponse>;
    getEntityReferences(entityType: 'document' | 'template' | 'terminology' | 'term' | 'file', entityId: string): Promise<EntityReferencesResponse>;
    getReferencedBy(entityType: 'document' | 'template' | 'terminology' | 'term' | 'file', entityId: string, limit?: number): Promise<ReferencedByResponse>;
}

interface WipClientConfig {
    baseUrl: string;
    auth?: AuthProvider | {
        type: 'api-key';
        key: string;
    } | {
        type: 'oidc';
        getToken: () => string | Promise<string>;
    };
    timeout?: number;
    retry?: RetryConfig;
    onAuthError?: () => void;
}
interface WipClient {
    defStore: DefStoreService;
    templates: TemplateStoreService;
    documents: DocumentStoreService;
    files: FileStoreService;
    registry: RegistryService;
    reporting: ReportingSyncService;
    setAuth(auth: AuthProvider): void;
}
declare function createWipClient(config: WipClientConfig): WipClient;

/**
 * Error hierarchy for WIP client operations.
 *
 * Maps HTTP status codes and bulk response errors to typed exceptions.
 */
declare class WipError extends Error {
    readonly statusCode?: number | undefined;
    readonly detail?: unknown | undefined;
    constructor(message: string, statusCode?: number | undefined, detail?: unknown | undefined);
}
declare class WipValidationError extends WipError {
    constructor(message: string, detail?: unknown);
}
declare class WipNotFoundError extends WipError {
    constructor(message: string, detail?: unknown);
}
declare class WipConflictError extends WipError {
    constructor(message: string, detail?: unknown);
}
declare class WipAuthError extends WipError {
    constructor(message: string, statusCode?: number, detail?: unknown);
}
declare class WipServerError extends WipError {
    constructor(message: string, statusCode?: number, detail?: unknown);
}
declare class WipNetworkError extends WipError {
    readonly cause?: Error | undefined;
    constructor(message: string, cause?: Error | undefined);
}
/** Thrown by single-item convenience methods when the bulk response item has status "error". */
declare class WipBulkItemError extends WipError {
    readonly index: number;
    readonly itemStatus: string;
    readonly errorCode?: string | undefined;
    readonly details?: Record<string, unknown> | undefined;
    constructor(message: string, index: number, itemStatus: string, errorCode?: string | undefined, details?: Record<string, unknown> | undefined);
}

/** Build a URL query string from a params object, handling undefined, arrays, and booleans. */
declare function buildQueryString(params: Record<string, unknown>): string;

type FormInputType = 'text' | 'number' | 'integer' | 'checkbox' | 'date' | 'datetime' | 'select' | 'search' | 'file' | 'group' | 'list';
interface FormField {
    name: string;
    label: string;
    inputType: FormInputType;
    required: boolean;
    defaultValue?: unknown;
    isIdentity: boolean;
    /** For term/select fields */
    terminologyCode?: string;
    /** For reference/search fields */
    referenceType?: string;
    targetTemplates?: string[];
    targetTerminologies?: string[];
    /** For file fields */
    fileConfig?: {
        allowedTypes: string[];
        maxSizeMb: number;
        multiple: boolean;
        maxFiles?: number;
    };
    /** For array fields */
    arrayItemType?: FormInputType;
    arrayTerminologyCode?: string;
    /** For object/group fields */
    children?: FormField[];
    /** Validation */
    validation?: {
        pattern?: string;
        minLength?: number;
        maxLength?: number;
        minimum?: number;
        maximum?: number;
        enum?: unknown[];
    };
    semanticType?: string;
}
/** Convert a WIP Template into a framework-agnostic form field descriptor array. */
declare function templateToFormSchema(template: Template): FormField[];

interface BulkImportProgress {
    processed: number;
    total: number;
    succeeded: number;
    failed: number;
}
interface BulkImportOptions {
    batchSize?: number;
    concurrency?: number;
    continueOnError?: boolean;
    onProgress?: (progress: BulkImportProgress) => void;
}
/**
 * Import items in batches, calling writeFn for each chunk.
 *
 * Supports concurrent batches via `concurrency` option (default: 1 = sequential).
 * Sequential mode is safest for Pi deployments; concurrency ≥ 2 improves throughput
 * on faster hardware by overlapping network I/O with server processing.
 */
declare function bulkImport<T>(items: T[], writeFn: (batch: T[]) => Promise<BulkResponse>, options?: BulkImportOptions): Promise<BulkImportProgress>;

interface ResolvedReference {
    documentId: string;
    displayValue: string;
    identityFields: Record<string, unknown>;
}
/**
 * Search for documents matching a reference field's target template.
 * Useful for populating reference field autocomplete.
 *
 * Fetches recent documents for the template and filters client-side
 * by search term. For large datasets, consider adding server-side
 * search to the document query endpoint.
 */
declare function resolveReference(client: WipClient, templateId: string, searchTerm: string, limit?: number): Promise<ResolvedReference[]>;

export { type APIKeyInfo, type APIKeyListResponse, type ActivateTemplateResponse, type ActivationDetail, type ActivityItem, type ActivityResponse, type AddSynonymRequest, type Alert, type AlertConfig, type AlertSeverity, type AlertThresholds, type AlertType, type AlertsResponse, type ApiError, ApiKeyAuthProvider, type AuditLogEntry, type AuditLogResponse, type AuthProvider, type BackupJobKind, type BackupJobSnapshot, type BackupJobStatus, type BackupProgressMessage, type BackupRequest, type BatchEntitySyncResult, type BatchJobCancelResult, type BatchJobsCleared, type BatchSyncJob, type BatchSyncRequest, type BatchSyncResponse, type BatchSyncStatus, type BulkImportOptions, type BulkImportProgress, type BulkResponse, type BulkResultItem, type BulkValidateRequest, type BulkValidateResponse, type BulkValidationResponse, type CascadeResponse, type CascadeResult, type ClashPolicy, type Condition, type ConditionOperator, type ConsumerInfo, type CreateAPIKeyRequest, type CreateAPIKeyResponse, type CreateDocumentRequest, type CreateGrantRequest, type CreateNamespaceRequest, type CreateTemplateRequest, type CreateTermRelationRequest, type CreateTermRequest, type CreateTerminologyRequest, type CrossVersionView, type CsvExportQuery, DefStoreService, type DeleteTermRelationRequest, type DeprecateTermRequest, type Document, type DocumentCreateResponse, type DocumentListResponse, type DocumentMetadata, type DocumentMigrateRequest, type DocumentMigrateResponse, type DocumentQueryParams, type DocumentQueryRequest, type DocumentReference, type DocumentRelationshipsParams, type DocumentStatus, DocumentStoreService, type DocumentTraverseNode, type DocumentTraverseParams, type DocumentTraverseResponse, type DocumentValidationResponse, type DocumentVersionResponse, type DocumentVersionSummary, type EntityDetails, type EntityReference, type EntityReferencesResponse, type ExportResponse, type ExportTerminologyResponse, FetchTransport, type FetchTransportConfig, type FieldDefinition, type FieldType, type FieldValidation, type FileDownloadResponse, type FileEntity, type FileFieldConfig, type FileIntegrityIssue, type FileIntegrityResponse, type FileListResponse, type FileMetadata, type FileQueryParams, type FileStatus, FileStoreService, type FileUploadMetadata, type FormField, type FormInputType, type Grant, type GrantBulkResponse, type GrantBulkResult, type GrantPermission, type GrantRevokeBulkResponse, type GrantRevokeResult, type GrantSubjectType, type HealthResponse, type IdAlgorithmConfig, type ImportDocumentError, type ImportDocumentResult, type ImportDocumentsOptions, type ImportDocumentsResponse, type ImportPreviewResponse, type ImportResponse, type ImportTerminologyRequest, type IncomingReference, type IntegrityCheckResult, type IntegrityIssue, type IntegritySummary, type LatencyStats, type ListAPIKeysParams, type ListBackupJobsParams, type MergeRequest, type MetricsResponse, type Namespace, type NamespaceIntegrityIssue, type NamespaceIntegrityResult, type NamespaceIntegritySummary, type NamespaceStats, OidcAuthProvider, type PaginatedResponse, type PatchDocumentRequest, type PeerProjection, type PerTemplateStats, type QueryFilter, type QueryFilterOperator, type Reference, type ReferenceType, type ReferencedByResponse, type RegistryBrowseParams, type RegistryByTermHit, type RegistryEntry, type RegistryEntryFull, type RegistryEntryListResponse, type RegistryLookupResponse, type RegistrySearchParams, type RegistrySearchResponse, type RegistrySearchResult, RegistryService, type RegistrySourceInfo, type RegistrySynonym, type RemoveSynonymRequest, type ReplayFilter, type ReplayRequest, type ReplaySessionResponse, type ReplayStatus, type ReportEntity, type ReportEntityVersion, type ReportQueryParams, type ReportQueryResult, type ReportTable, type ReportTableColumn, type ReportTableSchema, type ReportingConfig, ReportingSyncService, type ResolvedReference, type RestoreMode, type RestoreOptions, type RetryConfig, type RevokeGrantRequest, type RuleType, type SearchResponse, type SearchResult, type SearchTypeResults, type SemanticType, type SyncStatus, type SyncStrategy, type TableColumn, type TableViewParams, type TableViewResponse, type Template, type TemplateFacet, type TemplateFacetsParams, type TemplateFacetsResponse, type TemplateListResponse, type TemplateMetadata, type TemplateMigrationOffer, TemplateStoreService, type TemplateUpdateResponse, type TemplateUsage, type TemplateVersionEventDetails, type TemplateVersionImpact, type Term, type TermDocumentsResponse, type TermListResponse, type TermReference, type TermRelation, type TermRelationListResponse, type TermTranslation, type Terminology, type TerminologyListResponse, type TerminologyMetadata, type TraversalNode, type TraversalResponse, type UpdateAPIKeyRequest, type UpdateFileMetadataRequest, type UpdateNamespaceRequest, type UpdateTemplateRequest, type UpdateTermRequest, type UpdateTerminologyRequest, type ValidateDocumentRequest, type ValidateDocumentsRequest, type ValidateNamespaceParams, type ValidateTemplateRequest, type ValidateTemplateResponse, type ValidateValueRequest, type ValidateValueResponse, type ValidationRule, type VersionStrategy, WipAuthError, WipBulkItemError, type WipClient, type WipClientConfig, WipConflictError, WipError, WipNetworkError, WipNotFoundError, WipServerError, WipValidationError, asVersionEventDetails, buildQueryString, bulkImport, createWipClient, resolveReference, templateToFormSchema };
