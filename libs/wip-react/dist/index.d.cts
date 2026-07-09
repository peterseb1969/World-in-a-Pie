import * as react_jsx_runtime from 'react/jsx-runtime';
import { ReactNode, CSSProperties, ReactElement } from 'react';
import { WipClient, TerminologyListResponse, Terminology, Term, TermListResponse, Template, TemplateListResponse, Document, DocumentRelationshipsParams, DocumentListResponse, DocumentVersionResponse, DocumentQueryParams, DocumentQueryRequest, TableViewParams, TableViewResponse, DocumentTraverseParams, DocumentTraverseResponse, FileDownloadResponse, FileEntity, FileQueryParams, FileListResponse, Namespace, RegistrySearchParams, RegistrySearchResponse, ActivityResponse, BatchSyncJob, BatchJobCancelResult, BatchJobsCleared, IntegrityCheckResult, ReportQueryResult, SyncStatus, BatchSyncResponse, BatchEntitySyncResult, ActivateTemplateResponse, AddSynonymRequest, BulkResultItem, CreateDocumentRequest, BulkResponse, CreateNamespaceRequest, CreateTemplateRequest, CreateTermRequest, CreateTermRelationRequest, CreateTerminologyRequest, DeleteTermRelationRequest, DeprecateTermRequest, MergeRequest, RemoveSynonymRequest, PatchDocumentRequest, UpdateFileMetadataRequest, UpdateNamespaceRequest, UpdateTemplateRequest, UpdateTermRequest, UpdateTerminologyRequest, FileUploadMetadata, FormField, BulkImportProgress } from '@wip/client';
import * as _tanstack_react_query from '@tanstack/react-query';
import { UseQueryOptions, UseMutationOptions } from '@tanstack/react-query';

interface WipProviderProps {
    client: WipClient;
    children: ReactNode;
}
declare function WipProvider({ client, children }: WipProviderProps): react_jsx_runtime.JSX.Element;
declare function useWipClient(): WipClient;

interface WipFooterProps {
    /** When set, prepends "<appName> · " to the attribution text. */
    appName?: string;
    /** Layout-only override merged onto the wrapper <footer>. */
    className?: string;
    /** v1 ships 'compact' only. 'full' is reserved for v1.5. */
    variant?: 'compact' | 'full';
    /** Optional inline style override for the wrapper. */
    style?: CSSProperties;
    /**
     * Build timestamp baked at image-build time, e.g.
     * `import.meta.env.VITE_BUILD_STAMP`. When set (and not the local-dev
     * sentinel 'dev'), it renders as a muted suffix so an operator can see
     * which build a running app is (CASE-472). Omit/leave 'dev' to hide.
     */
    buildStamp?: string;
    /**
     * Short git SHA of the build, e.g. `import.meta.env.VITE_BUILD_SHA`.
     * Rendered alongside buildStamp when set.
     */
    buildSha?: string;
}
declare function WipFooter({ appName, className, variant, style, buildStamp, buildSha, }: WipFooterProps): ReactElement;

/** Query key factories for TanStack Query cache management. */
declare const wipKeys: {
    readonly all: readonly ["wip"];
    readonly terminologies: {
        readonly all: readonly ["wip", "terminologies"];
        readonly list: (params?: object) => readonly ["wip", "terminologies", "list", object | undefined];
        readonly detail: (id: string) => readonly ["wip", "terminologies", "detail", string];
    };
    readonly terms: {
        readonly all: readonly ["wip", "terms"];
        readonly list: (terminologyId: string, params?: object) => readonly ["wip", "terms", "list", string, object | undefined];
        readonly detail: (id: string) => readonly ["wip", "terms", "detail", string];
    };
    readonly templates: {
        readonly all: readonly ["wip", "templates"];
        readonly list: (params?: object) => readonly ["wip", "templates", "list", object | undefined];
        readonly detail: (id: string) => readonly ["wip", "templates", "detail", string];
        readonly byValue: (value: string) => readonly ["wip", "templates", "by-value", string];
    };
    readonly documents: {
        readonly all: readonly ["wip", "documents"];
        readonly list: (params?: object) => readonly ["wip", "documents", "list", object | undefined];
        readonly detail: (id: string) => readonly ["wip", "documents", "detail", string];
        readonly versions: (id: string) => readonly ["wip", "documents", "versions", string];
        readonly tableView: (templateId: string, params?: object) => readonly ["wip", "documents", "table", string, object | undefined];
        readonly relationships: (id: string, params?: object) => readonly ["wip", "documents", "relationships", string, object | undefined];
        readonly traverse: (id: string, params?: object) => readonly ["wip", "documents", "traverse", string, object | undefined];
    };
    readonly files: {
        readonly all: readonly ["wip", "files"];
        readonly list: (params?: object) => readonly ["wip", "files", "list", object | undefined];
        readonly detail: (id: string) => readonly ["wip", "files", "detail", string];
        readonly downloadUrl: (id: string) => readonly ["wip", "files", "download-url", string];
    };
    readonly registry: {
        readonly all: readonly ["wip", "registry"];
        readonly namespaces: () => readonly ["wip", "registry", "namespaces"];
        readonly namespace: (prefix: string) => readonly ["wip", "registry", "namespaces", string];
        readonly entries: (params?: object) => readonly ["wip", "registry", "entries", object | undefined];
        readonly entry: (id: string) => readonly ["wip", "registry", "entries", string];
        readonly search: (params?: object) => readonly ["wip", "registry", "search", object | undefined];
    };
    readonly reporting: {
        readonly all: readonly ["wip", "reporting"];
        readonly integrity: (params?: object) => readonly ["wip", "reporting", "integrity", object | undefined];
        readonly activity: (params?: object) => readonly ["wip", "reporting", "activity", object | undefined];
        readonly search: (params?: object) => readonly ["wip", "reporting", "search", object | undefined];
        readonly query: (sql: string, params?: unknown[], namespace?: string) => readonly ["wip", "reporting", "query", string, unknown[] | undefined, string | undefined];
        readonly syncStatus: () => readonly ["wip", "reporting", "sync-status"];
        readonly batchJobs: () => readonly ["wip", "reporting", "batch-jobs"];
        readonly batchJob: (jobId: string) => readonly ["wip", "reporting", "batch-jobs", string];
    };
};

/** Default stale times for different entity types (milliseconds). */
declare const STALE_TIMES: {
    /** Terminologies change rarely */
    readonly terminologies: number;
    /** Terms change rarely */
    readonly terms: number;
    /** Templates change rarely */
    readonly templates: number;
    /** Documents change more frequently */
    readonly documents: number;
    /** Files rarely change after upload */
    readonly files: number;
    /** Registry data is relatively stable */
    readonly registry: number;
    /** Reporting data may be slightly stale */
    readonly reporting: number;
};

declare function useTerminologies(params?: {
    page?: number;
    page_size?: number;
    status?: string;
    value?: string;
    namespace?: string;
}, options?: Omit<UseQueryOptions<TerminologyListResponse>, 'queryKey' | 'queryFn'>): _tanstack_react_query.UseQueryResult<TerminologyListResponse, Error>;
declare function useTerminology(id: string, options?: Omit<UseQueryOptions<Terminology>, 'queryKey' | 'queryFn'>): _tanstack_react_query.UseQueryResult<Terminology, Error>;

declare function useTerms(terminologyId: string, params?: {
    page?: number;
    page_size?: number;
    status?: string;
    search?: string;
}, options?: Omit<UseQueryOptions<TermListResponse>, 'queryKey' | 'queryFn'>): _tanstack_react_query.UseQueryResult<TermListResponse, Error>;
declare function useTerm(id: string, options?: Omit<UseQueryOptions<Term>, 'queryKey' | 'queryFn'>): _tanstack_react_query.UseQueryResult<Term, Error>;

declare function useTemplates(params?: {
    page?: number;
    page_size?: number;
    status?: string;
    extends?: string;
    value?: string;
    latest_only?: boolean;
    namespace?: string;
}, options?: Omit<UseQueryOptions<TemplateListResponse>, 'queryKey' | 'queryFn'>): _tanstack_react_query.UseQueryResult<TemplateListResponse, Error>;
declare function useTemplate(id: string, options?: Omit<UseQueryOptions<Template>, 'queryKey' | 'queryFn'>): _tanstack_react_query.UseQueryResult<Template, Error>;
declare function useTemplateByValue(value: string, options?: Omit<UseQueryOptions<Template>, 'queryKey' | 'queryFn'>): _tanstack_react_query.UseQueryResult<Template, Error>;

declare function useDocuments(params?: DocumentQueryParams, options?: Omit<UseQueryOptions<DocumentListResponse>, 'queryKey' | 'queryFn'>): _tanstack_react_query.UseQueryResult<DocumentListResponse, Error>;
declare function useDocument(id: string, options?: Omit<UseQueryOptions<Document>, 'queryKey' | 'queryFn'>): _tanstack_react_query.UseQueryResult<Document, Error>;
declare function useQueryDocuments(query: DocumentQueryRequest, options?: Omit<UseQueryOptions<DocumentListResponse>, 'queryKey' | 'queryFn'>): _tanstack_react_query.UseQueryResult<DocumentListResponse, Error>;
/**
 * Spreadsheet-style table view of a template's documents.
 *
 * Wraps `client.documents.getTableView` (document-store
 * `GET /table/{template_id}`): column definitions plus one row per
 * document, the same projection the CSV export uses. The queryKey is
 * `wipKeys.documents.tableView(templateId, params)` — declared since the
 * key hierarchy shipped, consumed by a first-party hook only now.
 *
 * Disabled when `templateId` is empty/falsy.
 */
declare function useTableView(templateId: string, params?: TableViewParams, options?: Omit<UseQueryOptions<TableViewResponse>, 'queryKey' | 'queryFn'>): _tanstack_react_query.UseQueryResult<TableViewResponse, Error>;
declare function useDocumentVersions(id: string, options?: Omit<UseQueryOptions<DocumentVersionResponse>, 'queryKey' | 'queryFn'>): _tanstack_react_query.UseQueryResult<DocumentVersionResponse, Error>;
/**
 * List relationship documents incident to a document (CASE-296).
 *
 * Wraps `client.documents.getDocumentRelationships`. Returns a
 * paginated list of relationship documents (templates with
 * `usage: 'relationship'`) pointing at (incoming) or from (outgoing)
 * the given document.
 *
 * Disabled when `documentId` is empty/falsy.
 */
declare function useDocumentRelationships(documentId: string, params?: DocumentRelationshipsParams, options?: Omit<UseQueryOptions<DocumentListResponse>, 'queryKey' | 'queryFn'>): _tanstack_react_query.UseQueryResult<DocumentListResponse, Error>;
/**
 * Traverse the relationship graph from a document (CASE-296).
 *
 * Wraps `client.documents.traverseDocuments`. BFS expansion through
 * relationship documents, capped at depth=10 and max_nodes=1000.
 * Check `data.truncated` to detect when a cap fired.
 *
 * Disabled when `documentId` is empty/falsy.
 */
declare function useTraverseDocuments(documentId: string, params?: DocumentTraverseParams, options?: Omit<UseQueryOptions<DocumentTraverseResponse>, 'queryKey' | 'queryFn'>): _tanstack_react_query.UseQueryResult<DocumentTraverseResponse, Error>;

declare function useFiles(params?: FileQueryParams, options?: Omit<UseQueryOptions<FileListResponse>, 'queryKey' | 'queryFn'>): _tanstack_react_query.UseQueryResult<FileListResponse, Error>;
declare function useFile(id: string, options?: Omit<UseQueryOptions<FileEntity>, 'queryKey' | 'queryFn'>): _tanstack_react_query.UseQueryResult<FileEntity, Error>;
declare function useDownloadUrl(id: string, options?: Omit<UseQueryOptions<FileDownloadResponse>, 'queryKey' | 'queryFn'>): _tanstack_react_query.UseQueryResult<FileDownloadResponse, Error>;

declare function useNamespaces(options?: Omit<UseQueryOptions<Namespace[]>, 'queryKey' | 'queryFn'>): _tanstack_react_query.UseQueryResult<Namespace[], Error>;
declare function useRegistrySearch(params: RegistrySearchParams, options?: Omit<UseQueryOptions<RegistrySearchResponse>, 'queryKey' | 'queryFn'>): _tanstack_react_query.UseQueryResult<RegistrySearchResponse, Error>;

/**
 * Execute a read-only SQL query against the PostgreSQL reporting layer.
 *
 * Use this for cross-entity aggregations, counts, and analytics.
 * Report queries are eventually consistent — don't use for state management.
 * Use the document/template hooks for authoritative current state.
 *
 * @example
 * // Simple aggregation
 * const { data } = useReportQuery(
 *   'SELECT location, COUNT(*) as scenes FROM aa_event GROUP BY location'
 * )
 *
 * @example
 * // Parameterized query with custom cache key
 * const { data } = useReportQuery(
 *   'SELECT * FROM aa_event WHERE location = $1',
 *   ['BLACKWOOD_MANOR'],
 *   { queryKey: ['cross-refs', 'location', 'BLACKWOOD_MANOR'] }
 * )
 *
 * @example
 * // Unqualified table names resolve in one namespace's PG schema
 * // (a reporting table is "<ns>"."doc_<value>"); for cross-namespace
 * // queries omit namespace and schema-qualify each table in the SQL.
 * const { data } = useReportQuery(
 *   'SELECT COUNT(*) FROM aa_event',
 *   undefined,
 *   { namespace: 'my-app' }
 * )
 */
declare function useReportQuery(sql: string, params?: unknown[], options?: Omit<UseQueryOptions<ReportQueryResult>, 'queryFn'> & {
    maxRows?: number;
    timeoutSeconds?: number;
    namespace?: string;
}): _tanstack_react_query.UseQueryResult<ReportQueryResult, Error>;
declare function useIntegrityCheck(params?: {
    template_status?: string;
    document_status?: string;
    template_limit?: number;
    document_limit?: number;
    check_term_refs?: boolean;
    recent_first?: boolean;
}, options?: Omit<UseQueryOptions<IntegrityCheckResult>, 'queryKey' | 'queryFn'>): _tanstack_react_query.UseQueryResult<IntegrityCheckResult, Error>;
declare function useActivity(params?: {
    types?: string;
    limit?: number;
}, options?: Omit<UseQueryOptions<ActivityResponse>, 'queryKey' | 'queryFn'>): _tanstack_react_query.UseQueryResult<ActivityResponse, Error>;
/**
 * Sync service status — running, NATS/Postgres connections,
 * events_processed/failed, tables_managed. Useful as a standalone
 * dashboard widget AND as a signal for the "first-time setup" CTA
 * (when tables_managed=0 but documents exist).
 *
 * @example
 *   const { data } = useSyncStatus({ refetchInterval: 5000 })
 */
declare function useSyncStatus(options?: Omit<UseQueryOptions<SyncStatus>, 'queryKey' | 'queryFn'>): _tanstack_react_query.UseQueryResult<SyncStatus, Error>;
/**
 * List all batch sync jobs. In-memory on the server — restarts of
 * reporting-sync clear the list. Pass `refetchInterval` for live
 * polling while jobs are in flight (CT_TRIAL_AE was 11min for 153k
 * docs; pick 2-5s as a reasonable default).
 *
 * @example
 *   const hasRunning = jobs?.some(j => j.status === 'running' || j.status === 'pending')
 *   const { data: jobs } = useBatchJobs({ refetchInterval: hasRunning ? 3000 : false })
 */
declare function useBatchJobs(options?: Omit<UseQueryOptions<BatchSyncJob[]>, 'queryKey' | 'queryFn'>): _tanstack_react_query.UseQueryResult<BatchSyncJob[], Error>;
/**
 * Single batch sync job by id. Pass `refetchInterval` for live
 * progress (`documents_synced` / `total_documents` / `current_page`).
 */
declare function useBatchJob(jobId: string, options?: Omit<UseQueryOptions<BatchSyncJob>, 'queryKey' | 'queryFn'>): _tanstack_react_query.UseQueryResult<BatchSyncJob, Error>;
type BatchSyncAllVars = {
    force?: boolean;
    page_size?: number;
} | void;
/** Trigger a batch sync for all templates with sync_enabled=true. */
declare function useTriggerBatchSyncAll(options?: Omit<UseMutationOptions<BatchSyncResponse[], Error, BatchSyncAllVars>, 'mutationFn'>): _tanstack_react_query.UseMutationResult<BatchSyncResponse[], Error, BatchSyncAllVars, unknown>;
type BatchSyncVars = {
    template_value: string;
    force?: boolean;
    page_size?: number;
};
/** Trigger a batch sync for a specific template. */
declare function useTriggerBatchSync(options?: Omit<UseMutationOptions<BatchSyncResponse, Error, BatchSyncVars>, 'mutationFn'>): _tanstack_react_query.UseMutationResult<BatchSyncResponse, Error, BatchSyncVars, unknown>;
type EntitySyncVars = {
    namespace: string;
    pageSize?: number;
};
/** Trigger a synchronous batch sync for the terminologies table. */
declare function useTriggerTerminologySync(options?: Omit<UseMutationOptions<BatchEntitySyncResult, Error, EntitySyncVars>, 'mutationFn'>): _tanstack_react_query.UseMutationResult<BatchEntitySyncResult, Error, EntitySyncVars, unknown>;
/** Trigger a synchronous batch sync for the terms table. */
declare function useTriggerTermSync(options?: Omit<UseMutationOptions<BatchEntitySyncResult, Error, EntitySyncVars>, 'mutationFn'>): _tanstack_react_query.UseMutationResult<BatchEntitySyncResult, Error, EntitySyncVars, unknown>;
/** Trigger a synchronous batch sync for the term_relations table. */
declare function useTriggerTermRelationSync(options?: Omit<UseMutationOptions<BatchEntitySyncResult, Error, EntitySyncVars>, 'mutationFn'>): _tanstack_react_query.UseMutationResult<BatchEntitySyncResult, Error, EntitySyncVars, unknown>;
/** Cancel a running batch sync job. */
declare function useCancelBatchJob(options?: Omit<UseMutationOptions<BatchJobCancelResult, Error, string>, 'mutationFn'>): _tanstack_react_query.UseMutationResult<BatchJobCancelResult, Error, string, unknown>;
/** Clear all completed/failed/cancelled jobs from in-memory state. */
declare function useClearCompletedJobs(options?: Omit<UseMutationOptions<BatchJobsCleared, Error, void>, 'mutationFn'>): _tanstack_react_query.UseMutationResult<BatchJobsCleared, Error, void, unknown>;

declare function useCreateTerminology(options?: Omit<UseMutationOptions<BulkResultItem, Error, CreateTerminologyRequest>, 'mutationFn'>): _tanstack_react_query.UseMutationResult<BulkResultItem, Error, CreateTerminologyRequest, unknown>;
declare function useUpdateTerminology(options?: Omit<UseMutationOptions<BulkResultItem, Error, {
    id: string;
    data: UpdateTerminologyRequest;
}>, 'mutationFn'>): _tanstack_react_query.UseMutationResult<BulkResultItem, Error, {
    id: string;
    data: UpdateTerminologyRequest;
}, unknown>;
declare function useDeleteTerminology(options?: Omit<UseMutationOptions<BulkResultItem, Error, string>, 'mutationFn'>): _tanstack_react_query.UseMutationResult<BulkResultItem, Error, string, unknown>;
declare function useCreateTerm(terminologyId: string, namespace: string, options?: Omit<UseMutationOptions<BulkResultItem, Error, CreateTermRequest>, 'mutationFn'>): _tanstack_react_query.UseMutationResult<BulkResultItem, Error, CreateTermRequest, unknown>;
declare function useUpdateTerm(options?: Omit<UseMutationOptions<BulkResultItem, Error, {
    termId: string;
    data: UpdateTermRequest;
}>, 'mutationFn'>): _tanstack_react_query.UseMutationResult<BulkResultItem, Error, {
    termId: string;
    data: UpdateTermRequest;
}, unknown>;
declare function useDeprecateTerm(options?: Omit<UseMutationOptions<BulkResultItem, Error, {
    termId: string;
    data: DeprecateTermRequest;
}>, 'mutationFn'>): _tanstack_react_query.UseMutationResult<BulkResultItem, Error, {
    termId: string;
    data: DeprecateTermRequest;
}, unknown>;
declare function useDeleteTerm(terminologyId: string, options?: Omit<UseMutationOptions<BulkResultItem, Error, string>, 'mutationFn'>): _tanstack_react_query.UseMutationResult<BulkResultItem, Error, string, unknown>;
declare function useCreateTemplate(options?: Omit<UseMutationOptions<BulkResultItem, Error, CreateTemplateRequest>, 'mutationFn'>): _tanstack_react_query.UseMutationResult<BulkResultItem, Error, CreateTemplateRequest, unknown>;
declare function useUpdateTemplate(options?: Omit<UseMutationOptions<BulkResultItem, Error, {
    id: string;
    data: UpdateTemplateRequest;
}>, 'mutationFn'>): _tanstack_react_query.UseMutationResult<BulkResultItem, Error, {
    id: string;
    data: UpdateTemplateRequest;
}, unknown>;
declare function useDeleteTemplate(options?: Omit<UseMutationOptions<BulkResultItem, Error, {
    id: string;
    updatedBy?: string;
    version?: number;
    force?: boolean;
    hardDelete?: boolean;
}>, 'mutationFn'>): _tanstack_react_query.UseMutationResult<BulkResultItem, Error, {
    id: string;
    updatedBy?: string;
    version?: number;
    force?: boolean;
    hardDelete?: boolean;
}, unknown>;
declare function useActivateTemplate(options?: Omit<UseMutationOptions<ActivateTemplateResponse, Error, {
    id: string;
    namespace: string;
    dry_run?: boolean;
}>, 'mutationFn'>): _tanstack_react_query.UseMutationResult<ActivateTemplateResponse, Error, {
    id: string;
    namespace: string;
    dry_run?: boolean;
}, unknown>;
declare function useReactivateTemplate(options?: Omit<UseMutationOptions<Template, Error, {
    id: string;
    version: number;
    namespace: string;
}>, 'mutationFn'>): _tanstack_react_query.UseMutationResult<Template, Error, {
    id: string;
    version: number;
    namespace: string;
}, unknown>;
declare function useAddEdgeTypeEndpoints(options?: Omit<UseMutationOptions<Template, Error, {
    id: string;
    namespace: string;
    addSourceTemplates?: string[];
    addTargetTemplates?: string[];
}>, 'mutationFn'>): _tanstack_react_query.UseMutationResult<Template, Error, {
    id: string;
    namespace: string;
    addSourceTemplates?: string[];
    addTargetTemplates?: string[];
}, unknown>;
declare function useCreateDocument(options?: Omit<UseMutationOptions<BulkResultItem, Error, CreateDocumentRequest>, 'mutationFn'>): _tanstack_react_query.UseMutationResult<BulkResultItem, Error, CreateDocumentRequest, unknown>;
declare function useCreateDocuments(options?: Omit<UseMutationOptions<BulkResponse, Error, CreateDocumentRequest[]>, 'mutationFn'>): _tanstack_react_query.UseMutationResult<BulkResponse, Error, CreateDocumentRequest[], unknown>;
/**
 * Apply an RFC 7396 JSON Merge Patch to a document.
 *
 * Wraps `client.documents.updateDocument`. Throws `WipBulkItemError`
 * (with `errorCode` populated) on per-item failure. Invalidates both
 * the document detail and the documents list on success.
 */
declare function useUpdateDocument(options?: Omit<UseMutationOptions<BulkResultItem, Error, {
    documentId: string;
    patch: Record<string, unknown>;
    ifMatch?: number;
}>, 'mutationFn'>): _tanstack_react_query.UseMutationResult<BulkResultItem, Error, {
    documentId: string;
    patch: Record<string, unknown>;
    ifMatch?: number;
}, unknown>;
/**
 * Bulk variant of {@link useUpdateDocument}. Returns the raw `BulkResponse`
 * so the caller can inspect per-item `error_code` values without per-item
 * exceptions interrupting the batch.
 */
declare function useUpdateDocuments(options?: Omit<UseMutationOptions<BulkResponse, Error, PatchDocumentRequest[]>, 'mutationFn'>): _tanstack_react_query.UseMutationResult<BulkResponse, Error, PatchDocumentRequest[], unknown>;
declare function useDeleteDocument(options?: Omit<UseMutationOptions<BulkResultItem, Error, {
    id: string;
    updatedBy?: string;
}>, 'mutationFn'>): _tanstack_react_query.UseMutationResult<BulkResultItem, Error, {
    id: string;
    updatedBy?: string;
}, unknown>;
declare function useArchiveDocument(options?: Omit<UseMutationOptions<BulkResultItem, Error, {
    id: string;
    archivedBy?: string;
}>, 'mutationFn'>): _tanstack_react_query.UseMutationResult<BulkResultItem, Error, {
    id: string;
    archivedBy?: string;
}, unknown>;
declare function useUploadFile(options?: Omit<UseMutationOptions<FileEntity, Error, {
    file: File | Blob;
    filename?: string;
    metadata?: FileUploadMetadata;
    namespace?: string;
}>, 'mutationFn'>): _tanstack_react_query.UseMutationResult<FileEntity, Error, {
    file: File | Blob;
    filename?: string;
    metadata?: FileUploadMetadata;
    namespace?: string;
}, unknown>;
declare function useUpdateFileMetadata(options?: Omit<UseMutationOptions<BulkResultItem, Error, {
    fileId: string;
    data: UpdateFileMetadataRequest;
}>, 'mutationFn'>): _tanstack_react_query.UseMutationResult<BulkResultItem, Error, {
    fileId: string;
    data: UpdateFileMetadataRequest;
}, unknown>;
declare function useDeleteFile(options?: Omit<UseMutationOptions<BulkResultItem, Error, string>, 'mutationFn'>): _tanstack_react_query.UseMutationResult<BulkResultItem, Error, string, unknown>;
declare function useDeleteFiles(options?: Omit<UseMutationOptions<BulkResponse, Error, string[]>, 'mutationFn'>): _tanstack_react_query.UseMutationResult<BulkResponse, Error, string[], unknown>;
declare function useHardDeleteFile(options?: Omit<UseMutationOptions<void, Error, string>, 'mutationFn'>): _tanstack_react_query.UseMutationResult<void, Error, string, unknown>;
declare function useCreateTermRelations(options?: Omit<UseMutationOptions<BulkResponse, Error, {
    items: CreateTermRelationRequest[];
    namespace: string;
}>, 'mutationFn'>): _tanstack_react_query.UseMutationResult<BulkResponse, Error, {
    items: CreateTermRelationRequest[];
    namespace: string;
}, unknown>;
declare function useDeleteTermRelations(options?: Omit<UseMutationOptions<BulkResponse, Error, {
    items: DeleteTermRelationRequest[];
    namespace: string;
}>, 'mutationFn'>): _tanstack_react_query.UseMutationResult<BulkResponse, Error, {
    items: DeleteTermRelationRequest[];
    namespace: string;
}, unknown>;
declare function useCreateNamespace(options?: Omit<UseMutationOptions<Namespace, Error, CreateNamespaceRequest>, 'mutationFn'>): _tanstack_react_query.UseMutationResult<Namespace, Error, CreateNamespaceRequest, unknown>;
declare function useUpdateNamespace(options?: Omit<UseMutationOptions<Namespace, Error, {
    prefix: string;
    data: UpdateNamespaceRequest;
}>, 'mutationFn'>): _tanstack_react_query.UseMutationResult<Namespace, Error, {
    prefix: string;
    data: UpdateNamespaceRequest;
}, unknown>;
declare function useArchiveNamespace(options?: Omit<UseMutationOptions<Namespace, Error, {
    prefix: string;
    archivedBy?: string;
}>, 'mutationFn'>): _tanstack_react_query.UseMutationResult<Namespace, Error, {
    prefix: string;
    archivedBy?: string;
}, unknown>;
declare function useRestoreNamespace(options?: Omit<UseMutationOptions<Namespace, Error, {
    prefix: string;
    restoredBy?: string;
}>, 'mutationFn'>): _tanstack_react_query.UseMutationResult<Namespace, Error, {
    prefix: string;
    restoredBy?: string;
}, unknown>;
declare function useDeleteNamespace(options?: Omit<UseMutationOptions<void, Error, {
    prefix: string;
    deletedBy?: string;
}>, 'mutationFn'>): _tanstack_react_query.UseMutationResult<void, Error, {
    prefix: string;
    deletedBy?: string;
}, unknown>;
declare function useAddSynonym(options?: Omit<UseMutationOptions<{
    status: string;
    registry_id?: string;
    error?: string;
}, Error, AddSynonymRequest>, 'mutationFn'>): _tanstack_react_query.UseMutationResult<{
    status: string;
    registry_id?: string;
    error?: string;
}, Error, AddSynonymRequest, unknown>;
declare function useRemoveSynonym(options?: Omit<UseMutationOptions<{
    status: string;
    registry_id?: string;
    error?: string;
}, Error, RemoveSynonymRequest>, 'mutationFn'>): _tanstack_react_query.UseMutationResult<{
    status: string;
    registry_id?: string;
    error?: string;
}, Error, RemoveSynonymRequest, unknown>;
declare function useMergeEntries(options?: Omit<UseMutationOptions<{
    status: string;
    preferred_id?: string;
    deprecated_id?: string;
    error?: string;
}, Error, MergeRequest>, 'mutationFn'>): _tanstack_react_query.UseMutationResult<{
    status: string;
    preferred_id?: string;
    deprecated_id?: string;
    error?: string;
}, Error, MergeRequest, unknown>;
declare function useDeactivateEntry(options?: Omit<UseMutationOptions<{
    status: string;
}, Error, {
    entryId: string;
    updatedBy?: string;
}>, 'mutationFn'>): _tanstack_react_query.UseMutationResult<{
    status: string;
}, Error, {
    entryId: string;
    updatedBy?: string;
}, unknown>;

/**
 * Fetch a template by value and convert it to a framework-agnostic form schema.
 */
declare function useFormSchema(templateValue: string, options?: Omit<UseQueryOptions<FormField[]>, 'queryKey' | 'queryFn'>): _tanstack_react_query.UseQueryResult<FormField[], Error>;

interface UseBulkImportOptions<T> {
    writeFn: (batch: T[]) => Promise<BulkResponse>;
    batchSize?: number;
    continueOnError?: boolean;
    invalidateKeys?: readonly unknown[];
}
declare function useBulkImport<T>(options: UseBulkImportOptions<T>): {
    progress: BulkImportProgress | null;
    reset: () => void;
    data: undefined;
    variables: undefined;
    error: null;
    isError: false;
    isIdle: true;
    isPending: false;
    isSuccess: false;
    status: "idle";
    mutate: _tanstack_react_query.UseMutateFunction<BulkImportProgress, Error, T[], unknown>;
    context: unknown;
    failureCount: number;
    failureReason: Error | null;
    isPaused: boolean;
    submittedAt: number;
    mutateAsync: _tanstack_react_query.UseMutateAsyncFunction<BulkImportProgress, Error, T[], unknown>;
} | {
    progress: BulkImportProgress | null;
    reset: () => void;
    data: undefined;
    variables: T[];
    error: null;
    isError: false;
    isIdle: false;
    isPending: true;
    isSuccess: false;
    status: "pending";
    mutate: _tanstack_react_query.UseMutateFunction<BulkImportProgress, Error, T[], unknown>;
    context: unknown;
    failureCount: number;
    failureReason: Error | null;
    isPaused: boolean;
    submittedAt: number;
    mutateAsync: _tanstack_react_query.UseMutateAsyncFunction<BulkImportProgress, Error, T[], unknown>;
} | {
    progress: BulkImportProgress | null;
    reset: () => void;
    data: undefined;
    error: Error;
    variables: T[];
    isError: true;
    isIdle: false;
    isPending: false;
    isSuccess: false;
    status: "error";
    mutate: _tanstack_react_query.UseMutateFunction<BulkImportProgress, Error, T[], unknown>;
    context: unknown;
    failureCount: number;
    failureReason: Error | null;
    isPaused: boolean;
    submittedAt: number;
    mutateAsync: _tanstack_react_query.UseMutateAsyncFunction<BulkImportProgress, Error, T[], unknown>;
} | {
    progress: BulkImportProgress | null;
    reset: () => void;
    data: BulkImportProgress;
    error: null;
    variables: T[];
    isError: false;
    isIdle: false;
    isPending: false;
    isSuccess: true;
    status: "success";
    mutate: _tanstack_react_query.UseMutateFunction<BulkImportProgress, Error, T[], unknown>;
    context: unknown;
    failureCount: number;
    failureReason: Error | null;
    isPaused: boolean;
    submittedAt: number;
    mutateAsync: _tanstack_react_query.UseMutateAsyncFunction<BulkImportProgress, Error, T[], unknown>;
};

export { STALE_TIMES, WipFooter, type WipFooterProps, WipProvider, type WipProviderProps, useActivateTemplate, useActivity, useAddEdgeTypeEndpoints, useAddSynonym, useArchiveDocument, useArchiveNamespace, useBatchJob, useBatchJobs, useBulkImport, useCancelBatchJob, useClearCompletedJobs, useCreateDocument, useCreateDocuments, useCreateNamespace, useCreateTemplate, useCreateTerm, useCreateTermRelations, useCreateTerminology, useDeactivateEntry, useDeleteDocument, useDeleteFile, useDeleteFiles, useDeleteNamespace, useDeleteTemplate, useDeleteTerm, useDeleteTermRelations, useDeleteTerminology, useDeprecateTerm, useDocument, useDocumentRelationships, useDocumentVersions, useDocuments, useDownloadUrl, useFile, useFiles, useFormSchema, useHardDeleteFile, useIntegrityCheck, useMergeEntries, useNamespaces, useQueryDocuments, useReactivateTemplate, useRegistrySearch, useRemoveSynonym, useReportQuery, useRestoreNamespace, useSyncStatus, useTableView, useTemplate, useTemplateByValue, useTemplates, useTerm, useTerminologies, useTerminology, useTerms, useTraverseDocuments, useTriggerBatchSync, useTriggerBatchSyncAll, useTriggerTermRelationSync, useTriggerTermSync, useTriggerTerminologySync, useUpdateDocument, useUpdateDocuments, useUpdateFileMetadata, useUpdateNamespace, useUpdateTemplate, useUpdateTerm, useUpdateTerminology, useUploadFile, useWipClient, wipKeys };
