declare class ApiKeyAuthProvider implements AuthProvider {
    private apiKey;
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
    relationships?: Array<{
        source_term_value: string;
        target_term_value: string;
        relationship_type: string;
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
    export_format: string;
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

interface Relationship {
    namespace: string;
    source_term_id: string;
    target_term_id: string;
    relationship_type: string;
    relationship_value?: string;
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
type RelationshipListResponse = PaginatedResponse<Relationship>;
interface CreateRelationshipRequest {
    source_term_id: string;
    target_term_id: string;
    relationship_type: string;
    metadata?: Record<string, unknown>;
    created_by?: string;
}
interface DeleteRelationshipRequest {
    source_term_id: string;
    target_term_id: string;
    relationship_type: string;
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
    relationship_type: string;
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
    }): Promise<TermListResponse>;
    getTerm(termId: string): Promise<Term>;
    createTerm(terminologyId: string, data: CreateTermRequest, options: {
        namespace: string;
    }): Promise<BulkResultItem>;
    createTerms(terminologyId: string, terms: CreateTermRequest[], options: {
        namespace: string;
        batch_size?: number;
        registry_batch_size?: number;
    }): Promise<BulkResponse>;
    updateTerm(termId: string, data: UpdateTermRequest): Promise<BulkResultItem>;
    deprecateTerm(termId: string, data: DeprecateTermRequest): Promise<BulkResultItem>;
    deleteTerm(termId: string, options?: {
        hardDelete?: boolean;
    }): Promise<BulkResultItem>;
    importTerminology(data: ImportTerminologyRequest): Promise<{
        terminology: Terminology;
        terms_result: BulkResponse;
        relationships_result?: {
            total: number;
            created: number;
            skipped: number;
            errors: number;
        };
    }>;
    exportTerminology(terminologyId: string, options?: {
        format?: 'json' | 'csv';
        includeInactive?: boolean;
        includeRelationships?: boolean;
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
        relationship_batch_size?: number;
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
        relationships: {
            total: number;
            created: number;
            skipped: number;
            errors: number;
            predicate_distribution: Record<string, number>;
            error_samples?: string[];
        };
        elapsed_seconds: number;
    }>;
    validateValue(data: ValidateValueRequest): Promise<ValidateValueResponse>;
    bulkValidate(data: BulkValidateRequest): Promise<BulkValidateResponse>;
    listRelationships(params: {
        term_id: string;
        direction?: string;
        relationship_type?: string;
        namespace?: string;
        page?: number;
        page_size?: number;
    }): Promise<RelationshipListResponse>;
    listAllRelationships(params?: {
        namespace?: string;
        relationship_type?: string;
        status?: string;
        page?: number;
        page_size?: number;
    }): Promise<RelationshipListResponse>;
    createRelationships(items: CreateRelationshipRequest[], namespace: string): Promise<BulkResponse>;
    deleteRelationships(items: DeleteRelationshipRequest[], namespace: string): Promise<BulkResponse>;
    getAncestors(termId: string, params?: {
        relationship_type?: string;
        namespace?: string;
        max_depth?: number;
    }): Promise<TraversalResponse>;
    getDescendants(termId: string, params?: {
        relationship_type?: string;
        namespace?: string;
        max_depth?: number;
    }): Promise<TraversalResponse>;
    getParents(termId: string, namespace: string): Promise<Relationship[]>;
    getChildren(termId: string, namespace: string): Promise<Relationship[]>;
    getTerminologyAuditLog(terminologyId: string, params?: {
        action?: string;
        page?: number;
        page_size?: number;
    }): Promise<AuditLogResponse>;
    getTermAuditLog(termId: string, params?: {
        action?: string;
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
    reference_type?: ReferenceType;
    target_templates?: string[];
    target_terminologies?: string[];
    version_strategy?: VersionStrategy;
    file_config?: FileFieldConfig;
    array_item_type?: FieldType;
    array_terminology_ref?: string;
    array_template_ref?: string;
    array_file_config?: FileFieldConfig;
    validation?: FieldValidation;
    semantic_type?: SemanticType;
    include_subtypes?: boolean;
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
interface ReportingConfig {
    sync_enabled: boolean;
    sync_strategy: SyncStrategy;
    table_name?: string;
    include_metadata: boolean;
    flatten_arrays: boolean;
    max_array_elements: number;
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
    }): Promise<TemplateListResponse>;
    getTemplate(id: string, version?: number): Promise<Template>;
    getTemplateRaw(id: string, version?: number): Promise<Template>;
    getTemplateByValue(value: string): Promise<Template>;
    getTemplateByValueRaw(value: string, namespace: string): Promise<Template>;
    getTemplateVersions(value: string): Promise<TemplateListResponse>;
    getTemplateByValueAndVersion(value: string, version: number): Promise<Template>;
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
    cascadeTemplate(id: string): Promise<CascadeResponse>;
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
     */
    patch: Record<string, unknown>;
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
interface DocumentVersionSummary {
    document_id: string;
    version: number;
    status: DocumentStatus;
    created_at: string;
    created_by: string | null;
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
 * Types for the document-store backup/restore endpoints (CASE-23 Phase 3 STEP 7).
 *
 * These mirror the wire contract defined by
 * `components/document-store/src/document_store/models/backup_job.py`.
 *
 * Guardrail 2: `BackupProgressMessage` is the SSE wire envelope. It is
 * deliberately decoupled from the internal `wip_toolkit.models.ProgressEvent`
 * so a future implementation can replace the toolkit without breaking clients.
 */
type BackupJobKind = 'backup' | 'restore';
type BackupJobStatus = 'pending' | 'running' | 'complete' | 'failed';
type RestoreMode = 'restore' | 'fresh';
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
    created_by: string;
}
/**
 * Request body for `POST /backup/namespaces/{namespace}/backup`.
 *
 * All fields map to keyword arguments of the underlying toolkit
 * `run_export` call. Defaults match the server side, so callers may pass an
 * empty object to take everything.
 *
 * **v1.0 caveat — `include_files`:** see CASE-28. Setting this to `true`
 * against any namespace with non-trivial file content currently causes the
 * archive writer to buffer all blobs in memory. Stick with the default
 * (`false`) until CASE-28 lands.
 */
interface BackupRequest {
    include_files?: boolean;
    include_inactive?: boolean;
    skip_documents?: boolean;
    skip_closure?: boolean;
    skip_synonyms?: boolean;
    latest_only?: boolean;
    template_prefixes?: string[];
    dry_run?: boolean;
}
/**
 * Form fields accompanying a multipart restore upload.
 *
 * **Mode gotcha:** `mode: 'restore'` ignores `target_namespace` and writes
 * back into the archive's source namespace. Use `mode: 'fresh'` (the default
 * here) when restoring into a *new* namespace — that path generates new IDs
 * and honours `target_namespace`.
 */
interface RestoreOptions {
    mode?: RestoreMode;
    target_namespace?: string;
    register_synonyms?: boolean;
    skip_documents?: boolean;
    skip_files?: boolean;
    batch_size?: number;
    continue_on_error?: boolean;
    dry_run?: boolean;
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
    getDocument(id: string, version?: number): Promise<Document>;
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
    archiveDocument(id: string, archivedBy?: string): Promise<BulkResultItem>;
    validateDocument(data: ValidateDocumentRequest): Promise<DocumentValidationResponse>;
    getVersions(id: string): Promise<DocumentVersionResponse>;
    getVersion(id: string, version: number): Promise<Document>;
    getTableView(templateId: string, params?: TableViewParams): Promise<TableViewResponse>;
    exportTableCsv(templateId: string, params?: {
        status?: string;
        include_metadata?: boolean;
        max_cross_product?: number;
    }): Promise<Blob>;
    getLatestDocument(id: string): Promise<Document>;
    getDocumentByIdentity(identityHash: string, includeInactive?: boolean): Promise<Document>;
    queryDocuments(body: DocumentQueryRequest): Promise<DocumentListResponse>;
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
     * Restore a namespace from an uploaded archive. The archive is streamed
     * to disk on the server, so multi-GB uploads do not buffer in memory.
     *
     * **Mode gotcha:** `mode: 'restore'` writes back into the archive's source
     * namespace and ignores `target_namespace`. Use `mode: 'fresh'` when
     * restoring into a different namespace.
     */
    startRestore(namespace: string, archive: Blob | File, options?: RestoreOptions, filename?: string): Promise<BackupJobSnapshot>;
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
    uploadFile(file: File | Blob, filename?: string, metadata?: FileUploadMetadata): Promise<FileEntity>;
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
interface RegistryLookupResponse {
    input_index: number;
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
}
interface CreateAPIKeyRequest {
    name: string;
    owner?: string;
    groups?: string[];
    namespaces?: string[] | null;
    description?: string;
    expires_at?: string;
}
interface CreateAPIKeyResponse extends APIKeyInfo {
    plaintext_key: string;
}
interface UpdateAPIKeyRequest {
    description?: string;
    groups?: string[];
    namespaces?: string[] | null;
    expires_at?: string;
    enabled?: boolean;
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
    searchEntries(term: string, options?: {
        namespaces?: string[];
        entityTypes?: string[];
        includeInactive?: boolean;
    }): Promise<RegistryLookupResponse[]>;
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
    listAPIKeys(): Promise<APIKeyInfo[]>;
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
interface ReportTable {
    table_name: string;
    row_count: number;
}
interface ReportTableSchema {
    template_value: string;
    table_name: string;
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
    status: BatchSyncStatus;
    message: string;
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
}
interface SearchResponse {
    query: string;
    results: SearchResult[];
    counts: Record<string, number>;
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
    /** Execute a read-only SQL query against the PostgreSQL reporting database */
    runQuery(sql: string, params?: unknown[], options?: {
        timeout_seconds?: number;
        max_rows?: number;
    }): Promise<ReportQueryResult>;
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
    /** List all PostgreSQL reporting tables */
    listTables(tableName?: string): Promise<{
        tables: ReportTable[];
    }>;
    /** Get PostgreSQL schema for a template's reporting table */
    getTableSchema(templateValue: string): Promise<ReportTableSchema>;
    getIntegrityCheck(params?: {
        template_status?: string;
        document_status?: string;
        template_limit?: number;
        document_limit?: number;
        check_term_refs?: boolean;
        recent_first?: boolean;
    }): Promise<IntegrityCheckResult>;
    search(params: {
        query: string;
        types?: string[];
        namespace: string;
        status?: string;
        limit?: number;
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

export { type APIKeyInfo, type ActivateTemplateResponse, type ActivationDetail, type ActivityItem, type ActivityResponse, type AddSynonymRequest, type Alert, type AlertConfig, type AlertSeverity, type AlertThresholds, type AlertType, type AlertsResponse, type ApiError, ApiKeyAuthProvider, type AuditLogEntry, type AuditLogResponse, type AuthProvider, type BackupJobKind, type BackupJobSnapshot, type BackupJobStatus, type BackupProgressMessage, type BackupRequest, type BatchSyncJob, type BatchSyncRequest, type BatchSyncResponse, type BatchSyncStatus, type BulkImportOptions, type BulkImportProgress, type BulkResponse, type BulkResultItem, type BulkValidateRequest, type BulkValidateResponse, type CascadeResponse, type CascadeResult, type Condition, type ConditionOperator, type ConsumerInfo, type CreateAPIKeyRequest, type CreateAPIKeyResponse, type CreateDocumentRequest, type CreateGrantRequest, type CreateNamespaceRequest, type CreateRelationshipRequest, type CreateTemplateRequest, type CreateTermRequest, type CreateTerminologyRequest, type CsvExportQuery, DefStoreService, type DeleteRelationshipRequest, type DeprecateTermRequest, type Document, type DocumentCreateResponse, type DocumentListResponse, type DocumentMetadata, type DocumentQueryParams, type DocumentQueryRequest, type DocumentReference, type DocumentStatus, DocumentStoreService, type DocumentValidationResponse, type DocumentVersionResponse, type DocumentVersionSummary, type EntityDetails, type EntityReference, type EntityReferencesResponse, type ExportResponse, type ExportTerminologyResponse, FetchTransport, type FetchTransportConfig, type FieldDefinition, type FieldType, type FieldValidation, type FileDownloadResponse, type FileEntity, type FileFieldConfig, type FileIntegrityIssue, type FileIntegrityResponse, type FileListResponse, type FileMetadata, type FileQueryParams, type FileStatus, FileStoreService, type FileUploadMetadata, type FormField, type FormInputType, type Grant, type GrantBulkResponse, type GrantBulkResult, type GrantPermission, type GrantRevokeBulkResponse, type GrantRevokeResult, type GrantSubjectType, type HealthResponse, type IdAlgorithmConfig, type ImportDocumentError, type ImportDocumentResult, type ImportDocumentsOptions, type ImportDocumentsResponse, type ImportPreviewResponse, type ImportResponse, type ImportTerminologyRequest, type IncomingReference, type IntegrityCheckResult, type IntegrityIssue, type IntegritySummary, type LatencyStats, type ListBackupJobsParams, type MergeRequest, type MetricsResponse, type Namespace, type NamespaceStats, OidcAuthProvider, type PaginatedResponse, type PatchDocumentRequest, type PerTemplateStats, type QueryFilter, type QueryFilterOperator, type Reference, type ReferenceType, type ReferencedByResponse, type RegistryBrowseParams, type RegistryEntry, type RegistryEntryFull, type RegistryEntryListResponse, type RegistryLookupResponse, type RegistrySearchParams, type RegistrySearchResponse, type RegistrySearchResult, RegistryService, type RegistrySourceInfo, type RegistrySynonym, type Relationship, type RelationshipListResponse, type RemoveSynonymRequest, type ReplayFilter, type ReplayRequest, type ReplaySessionResponse, type ReplayStatus, type ReportQueryParams, type ReportQueryResult, type ReportTable, type ReportTableColumn, type ReportTableSchema, type ReportingConfig, ReportingSyncService, type ResolvedReference, type RestoreMode, type RestoreOptions, type RetryConfig, type RevokeGrantRequest, type RuleType, type SearchResponse, type SearchResult, type SemanticType, type SyncStatus, type SyncStrategy, type TableColumn, type TableViewParams, type TableViewResponse, type Template, type TemplateListResponse, type TemplateMetadata, TemplateStoreService, type TemplateUpdateResponse, type Term, type TermDocumentsResponse, type TermListResponse, type TermReference, type TermTranslation, type Terminology, type TerminologyListResponse, type TerminologyMetadata, type TraversalNode, type TraversalResponse, type UpdateAPIKeyRequest, type UpdateFileMetadataRequest, type UpdateNamespaceRequest, type UpdateTemplateRequest, type UpdateTermRequest, type UpdateTerminologyRequest, type ValidateDocumentRequest, type ValidateTemplateRequest, type ValidateTemplateResponse, type ValidateValueRequest, type ValidateValueResponse, type ValidationRule, type VersionStrategy, WipAuthError, WipBulkItemError, type WipClient, type WipClientConfig, WipConflictError, WipError, WipNetworkError, WipNotFoundError, WipServerError, WipValidationError, buildQueryString, bulkImport, createWipClient, resolveReference, templateToFormSchema };
