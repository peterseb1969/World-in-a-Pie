'use strict';

var react = require('react');
var jsxRuntime = require('react/jsx-runtime');
var reactQuery = require('@tanstack/react-query');
var client = require('@wip/client');

// src/provider.tsx
var WipClientContext = react.createContext(null);
function WipProvider({ client, children }) {
  return /* @__PURE__ */ jsxRuntime.jsx(WipClientContext.Provider, { value: client, children });
}
function useWipClient() {
  const client = react.useContext(WipClientContext);
  if (!client) {
    throw new Error("useWipClient must be used within a <WipProvider>");
  }
  return client;
}
var WRAPPER_BASE = "mt-12 border-t border-gray-200 py-4";
var INNER = "mx-auto flex max-w-6xl items-center justify-center gap-2 px-4 text-xs text-text-muted";
var LOGO_CLASS = "h-4 w-auto";
function joinClassNames(base, override) {
  return override ? `${base} ${override}` : base;
}
function WipFooter({
  appName,
  className,
  variant = "compact",
  style,
  buildStamp,
  buildSha
}) {
  const text = appName ? `${appName} \xB7 Built on WIP` : "Built on WIP";
  const stamp = buildStamp && buildStamp !== "dev" ? buildStamp : void 0;
  const sha = buildSha && buildSha !== "dev" ? buildSha : void 0;
  const buildText = [stamp, sha].filter(Boolean).join(" \xB7 ");
  return /* @__PURE__ */ jsxRuntime.jsx("footer", { className: joinClassNames(WRAPPER_BASE, className), style, "data-wip-footer-variant": variant, children: /* @__PURE__ */ jsxRuntime.jsxs("div", { className: INNER, children: [
    /* @__PURE__ */ jsxRuntime.jsxs(
      "svg",
      {
        viewBox: "0 0 100 100",
        width: "16",
        height: "16",
        className: LOGO_CLASS,
        "aria-hidden": "true",
        focusable: "false",
        children: [
          /* @__PURE__ */ jsxRuntime.jsx("path", { d: "M50 50 L90 50 A40 40 0 0 1 74 82 L26 82 A40 40 0 0 1 50 10 Z", fill: "#2B579A" }),
          /* @__PURE__ */ jsxRuntime.jsx("rect", { x: "5", y: "86", width: "90", height: "6", rx: "3", fill: "#2B579A" })
        ]
      }
    ),
    /* @__PURE__ */ jsxRuntime.jsx("span", { children: text }),
    buildText ? /* @__PURE__ */ jsxRuntime.jsx("span", { className: "opacity-60", "data-wip-build-stamp": true, title: "Build of the running image", children: `\xB7 ${buildText}` }) : null
  ] }) });
}

// src/utils/keys.ts
var wipKeys = {
  all: ["wip"],
  terminologies: {
    all: ["wip", "terminologies"],
    list: (params) => ["wip", "terminologies", "list", params],
    detail: (id) => ["wip", "terminologies", "detail", id]
  },
  terms: {
    all: ["wip", "terms"],
    list: (terminologyId, params) => ["wip", "terms", "list", terminologyId, params],
    detail: (id) => ["wip", "terms", "detail", id]
  },
  templates: {
    all: ["wip", "templates"],
    list: (params) => ["wip", "templates", "list", params],
    detail: (id) => ["wip", "templates", "detail", id],
    byValue: (value) => ["wip", "templates", "by-value", value]
  },
  documents: {
    all: ["wip", "documents"],
    list: (params) => ["wip", "documents", "list", params],
    detail: (id) => ["wip", "documents", "detail", id],
    versions: (id) => ["wip", "documents", "versions", id],
    tableView: (templateId, params) => ["wip", "documents", "table", templateId, params],
    relationships: (id, params) => ["wip", "documents", "relationships", id, params],
    traverse: (id, params) => ["wip", "documents", "traverse", id, params]
  },
  files: {
    all: ["wip", "files"],
    list: (params) => ["wip", "files", "list", params],
    detail: (id) => ["wip", "files", "detail", id],
    downloadUrl: (id) => ["wip", "files", "download-url", id]
  },
  registry: {
    all: ["wip", "registry"],
    namespaces: () => ["wip", "registry", "namespaces"],
    namespace: (prefix) => ["wip", "registry", "namespaces", prefix],
    entries: (params) => ["wip", "registry", "entries", params],
    entry: (id) => ["wip", "registry", "entries", id],
    search: (params) => ["wip", "registry", "search", params]
  },
  reporting: {
    all: ["wip", "reporting"],
    integrity: (params) => ["wip", "reporting", "integrity", params],
    activity: (params) => ["wip", "reporting", "activity", params],
    search: (params) => ["wip", "reporting", "search", params],
    // namespace is part of the key: the same SQL against two namespaces
    // returns different data (per-namespace PG schemas), so a
    // namespace-blind key would serve stale cross-namespace cache hits.
    query: (sql, params, namespace) => ["wip", "reporting", "query", sql, params, namespace],
    syncStatus: () => ["wip", "reporting", "sync-status"],
    batchJobs: () => ["wip", "reporting", "batch-jobs"],
    batchJob: (jobId) => ["wip", "reporting", "batch-jobs", jobId]
  }
};

// src/utils/defaults.ts
var STALE_TIMES = {
  /** Terminologies change rarely */
  terminologies: 5 * 60 * 1e3,
  /** Terms change rarely */
  terms: 5 * 60 * 1e3,
  /** Templates change rarely */
  templates: 5 * 60 * 1e3,
  /** Documents change more frequently */
  documents: 30 * 1e3,
  /** Files rarely change after upload */
  files: 10 * 60 * 1e3,
  /** Registry data is relatively stable */
  registry: 5 * 60 * 1e3,
  /** Reporting data may be slightly stale */
  reporting: 60 * 1e3
};
function useTerminologies(params, options) {
  const client = useWipClient();
  return reactQuery.useQuery({
    queryKey: wipKeys.terminologies.list(params),
    queryFn: () => client.defStore.listTerminologies(params),
    staleTime: STALE_TIMES.terminologies,
    ...options
  });
}
function useTerminology(id, options) {
  const client = useWipClient();
  return reactQuery.useQuery({
    queryKey: wipKeys.terminologies.detail(id),
    queryFn: () => client.defStore.getTerminology(id),
    staleTime: STALE_TIMES.terminologies,
    enabled: !!id,
    ...options
  });
}
function useTerms(terminologyId, params, options) {
  const client = useWipClient();
  return reactQuery.useQuery({
    queryKey: wipKeys.terms.list(terminologyId, params),
    queryFn: () => client.defStore.listTerms(terminologyId, params),
    staleTime: STALE_TIMES.terms,
    enabled: !!terminologyId,
    ...options
  });
}
function useTerm(id, options) {
  const client = useWipClient();
  return reactQuery.useQuery({
    queryKey: wipKeys.terms.detail(id),
    queryFn: () => client.defStore.getTerm(id),
    staleTime: STALE_TIMES.terms,
    enabled: !!id,
    ...options
  });
}
function useTemplates(params, options) {
  const client = useWipClient();
  return reactQuery.useQuery({
    queryKey: wipKeys.templates.list(params),
    queryFn: () => client.templates.listTemplates(params),
    staleTime: STALE_TIMES.templates,
    ...options
  });
}
function useTemplate(id, options) {
  const client = useWipClient();
  return reactQuery.useQuery({
    queryKey: wipKeys.templates.detail(id),
    queryFn: () => client.templates.getTemplate(id),
    staleTime: STALE_TIMES.templates,
    enabled: !!id,
    ...options
  });
}
function useTemplateByValue(value, options) {
  const client = useWipClient();
  return reactQuery.useQuery({
    queryKey: wipKeys.templates.byValue(value),
    queryFn: () => client.templates.getTemplateByValue(value),
    staleTime: STALE_TIMES.templates,
    enabled: !!value,
    ...options
  });
}
function useDocuments(params, options) {
  const client = useWipClient();
  return reactQuery.useQuery({
    queryKey: wipKeys.documents.list(params),
    queryFn: () => client.documents.listDocuments(params),
    staleTime: STALE_TIMES.documents,
    ...options
  });
}
function useDocument(id, options) {
  const client = useWipClient();
  return reactQuery.useQuery({
    queryKey: wipKeys.documents.detail(id),
    queryFn: () => client.documents.getDocument(id),
    staleTime: STALE_TIMES.documents,
    enabled: !!id,
    ...options
  });
}
function useQueryDocuments(query, options) {
  const client = useWipClient();
  return reactQuery.useQuery({
    queryKey: [...wipKeys.documents.all, "query", query],
    queryFn: () => client.documents.queryDocuments(query),
    staleTime: STALE_TIMES.documents,
    enabled: !!(query.template_id || query.filters && query.filters.length > 0),
    ...options
  });
}
function useDocumentVersions(id, options) {
  const client = useWipClient();
  return reactQuery.useQuery({
    queryKey: wipKeys.documents.versions(id),
    queryFn: () => client.documents.getVersions(id),
    staleTime: STALE_TIMES.documents,
    enabled: !!id,
    ...options
  });
}
function useDocumentRelationships(documentId, params, options) {
  const client = useWipClient();
  return reactQuery.useQuery({
    queryKey: wipKeys.documents.relationships(documentId, params),
    queryFn: () => client.documents.getDocumentRelationships(documentId, params),
    staleTime: STALE_TIMES.documents,
    enabled: !!documentId,
    ...options
  });
}
function useTraverseDocuments(documentId, params, options) {
  const client = useWipClient();
  return reactQuery.useQuery({
    queryKey: wipKeys.documents.traverse(documentId, params),
    queryFn: () => client.documents.traverseDocuments(documentId, params),
    staleTime: STALE_TIMES.documents,
    enabled: !!documentId,
    ...options
  });
}
function useFiles(params, options) {
  const client = useWipClient();
  return reactQuery.useQuery({
    queryKey: wipKeys.files.list(params),
    queryFn: () => client.files.listFiles(params),
    staleTime: STALE_TIMES.files,
    ...options
  });
}
function useFile(id, options) {
  const client = useWipClient();
  return reactQuery.useQuery({
    queryKey: wipKeys.files.detail(id),
    queryFn: () => client.files.getFile(id),
    staleTime: STALE_TIMES.files,
    enabled: !!id,
    ...options
  });
}
function useDownloadUrl(id, options) {
  const client = useWipClient();
  return reactQuery.useQuery({
    queryKey: wipKeys.files.downloadUrl(id),
    queryFn: () => client.files.getDownloadUrl(id),
    staleTime: 60 * 1e3,
    // URLs expire — shorter stale time
    enabled: !!id,
    ...options
  });
}
function useNamespaces(options) {
  const client = useWipClient();
  return reactQuery.useQuery({
    queryKey: wipKeys.registry.namespaces(),
    queryFn: () => client.registry.listNamespaces(),
    staleTime: STALE_TIMES.registry,
    ...options
  });
}
function useRegistrySearch(params, options) {
  const client = useWipClient();
  return reactQuery.useQuery({
    queryKey: wipKeys.registry.search(params),
    queryFn: () => client.registry.unifiedSearch(params),
    staleTime: STALE_TIMES.registry,
    enabled: !!params.q,
    ...options
  });
}
var REPORT_QUERY_STALE_TIME = 1e4;
function useReportQuery(sql, params, options) {
  const client = useWipClient();
  const { maxRows, timeoutSeconds, namespace, ...queryOptions } = options ?? {};
  return reactQuery.useQuery({
    queryKey: wipKeys.reporting.query(sql, params, namespace),
    queryFn: () => client.reporting.runQuery(sql, params, {
      max_rows: maxRows,
      timeout_seconds: timeoutSeconds,
      namespace
    }),
    staleTime: REPORT_QUERY_STALE_TIME,
    ...queryOptions
  });
}
function useIntegrityCheck(params, options) {
  const client = useWipClient();
  return reactQuery.useQuery({
    queryKey: wipKeys.reporting.integrity(params),
    queryFn: () => client.reporting.getIntegrityCheck(params),
    staleTime: STALE_TIMES.reporting,
    ...options
  });
}
function useActivity(params, options) {
  const client = useWipClient();
  return reactQuery.useQuery({
    queryKey: wipKeys.reporting.activity(params),
    queryFn: () => client.reporting.getRecentActivity(params),
    staleTime: STALE_TIMES.reporting,
    ...options
  });
}
function useSyncStatus(options) {
  const client = useWipClient();
  return reactQuery.useQuery({
    queryKey: wipKeys.reporting.syncStatus(),
    queryFn: () => client.reporting.getSyncStatus(),
    staleTime: STALE_TIMES.reporting,
    ...options
  });
}
function useBatchJobs(options) {
  const client = useWipClient();
  return reactQuery.useQuery({
    queryKey: wipKeys.reporting.batchJobs(),
    queryFn: () => client.reporting.listBatchJobs(),
    staleTime: 0,
    // jobs are live state — always refetch
    ...options
  });
}
function useBatchJob(jobId, options) {
  const client = useWipClient();
  return reactQuery.useQuery({
    queryKey: wipKeys.reporting.batchJob(jobId),
    queryFn: () => client.reporting.getBatchJob(jobId),
    staleTime: 0,
    enabled: Boolean(jobId),
    ...options
  });
}
function useTriggerBatchSyncAll(options) {
  const client = useWipClient();
  const queryClient = reactQuery.useQueryClient();
  const { onSuccess, ...rest } = options ?? {};
  return reactQuery.useMutation({
    ...rest,
    mutationFn: (vars) => client.reporting.triggerBatchSyncAll(vars ?? void 0),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.reporting.batchJobs() });
      onSuccess?.(...args);
    }
  });
}
function useTriggerBatchSync(options) {
  const client = useWipClient();
  const queryClient = reactQuery.useQueryClient();
  const { onSuccess, ...rest } = options ?? {};
  return reactQuery.useMutation({
    ...rest,
    mutationFn: ({ template_value, force, page_size }) => client.reporting.triggerBatchSync(template_value, { force, page_size }),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.reporting.batchJobs() });
      onSuccess?.(...args);
    }
  });
}
function useTriggerTerminologySync(options) {
  const client = useWipClient();
  return reactQuery.useMutation({
    ...options,
    mutationFn: ({ namespace, pageSize }) => client.reporting.triggerTerminologySync(namespace, pageSize)
  });
}
function useTriggerTermSync(options) {
  const client = useWipClient();
  return reactQuery.useMutation({
    ...options,
    mutationFn: ({ namespace, pageSize }) => client.reporting.triggerTermSync(namespace, pageSize)
  });
}
function useTriggerTermRelationSync(options) {
  const client = useWipClient();
  return reactQuery.useMutation({
    ...options,
    mutationFn: ({ namespace, pageSize }) => client.reporting.triggerTermRelationSync(namespace, pageSize)
  });
}
function useCancelBatchJob(options) {
  const client = useWipClient();
  const queryClient = reactQuery.useQueryClient();
  const { onSuccess, ...rest } = options ?? {};
  return reactQuery.useMutation({
    ...rest,
    mutationFn: (jobId) => client.reporting.cancelBatchJob(jobId),
    onSuccess: (...args) => {
      const jobId = args[1];
      queryClient.invalidateQueries({ queryKey: wipKeys.reporting.batchJobs() });
      queryClient.invalidateQueries({ queryKey: wipKeys.reporting.batchJob(jobId) });
      onSuccess?.(...args);
    }
  });
}
function useClearCompletedJobs(options) {
  const client = useWipClient();
  const queryClient = reactQuery.useQueryClient();
  const { onSuccess, ...rest } = options ?? {};
  return reactQuery.useMutation({
    ...rest,
    mutationFn: () => client.reporting.clearCompletedJobs(),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.reporting.batchJobs() });
      onSuccess?.(...args);
    }
  });
}
function useCreateTerminology(options) {
  const { onSuccess, ...restOptions } = options ?? {};
  const client = useWipClient();
  const queryClient = reactQuery.useQueryClient();
  return reactQuery.useMutation({
    ...restOptions,
    mutationFn: (data) => client.defStore.createTerminology(data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.terminologies.all });
      onSuccess?.(...args);
    }
  });
}
function useUpdateTerminology(options) {
  const { onSuccess, ...restOptions } = options ?? {};
  const client = useWipClient();
  const queryClient = reactQuery.useQueryClient();
  return reactQuery.useMutation({
    ...restOptions,
    mutationFn: ({ id, data }) => client.defStore.updateTerminology(id, data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.terminologies.all });
      onSuccess?.(...args);
    }
  });
}
function useDeleteTerminology(options) {
  const { onSuccess, ...restOptions } = options ?? {};
  const client = useWipClient();
  const queryClient = reactQuery.useQueryClient();
  return reactQuery.useMutation({
    ...restOptions,
    mutationFn: (id) => client.defStore.deleteTerminology(id),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.terminologies.all });
      onSuccess?.(...args);
    }
  });
}
function useCreateTerm(terminologyId, namespace, options) {
  const { onSuccess, ...restOptions } = options ?? {};
  const client = useWipClient();
  const queryClient = reactQuery.useQueryClient();
  return reactQuery.useMutation({
    ...restOptions,
    mutationFn: (data) => client.defStore.createTerm(terminologyId, data, { namespace }),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.terms.all });
      queryClient.invalidateQueries({ queryKey: wipKeys.terminologies.detail(terminologyId) });
      onSuccess?.(...args);
    }
  });
}
function useUpdateTerm(options) {
  const { onSuccess, ...restOptions } = options ?? {};
  const client = useWipClient();
  const queryClient = reactQuery.useQueryClient();
  return reactQuery.useMutation({
    ...restOptions,
    mutationFn: ({ termId, data }) => client.defStore.updateTerm(termId, data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.terms.all });
      onSuccess?.(...args);
    }
  });
}
function useDeprecateTerm(options) {
  const { onSuccess, ...restOptions } = options ?? {};
  const client = useWipClient();
  const queryClient = reactQuery.useQueryClient();
  return reactQuery.useMutation({
    ...restOptions,
    mutationFn: ({ termId, data }) => client.defStore.deprecateTerm(termId, data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.terms.all });
      onSuccess?.(...args);
    }
  });
}
function useDeleteTerm(terminologyId, options) {
  const { onSuccess, ...restOptions } = options ?? {};
  const client = useWipClient();
  const queryClient = reactQuery.useQueryClient();
  return reactQuery.useMutation({
    ...restOptions,
    mutationFn: (termId) => client.defStore.deleteTerm(termId),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.terms.all });
      queryClient.invalidateQueries({ queryKey: wipKeys.terminologies.detail(terminologyId) });
      onSuccess?.(...args);
    }
  });
}
function useCreateTemplate(options) {
  const { onSuccess, ...restOptions } = options ?? {};
  const client = useWipClient();
  const queryClient = reactQuery.useQueryClient();
  return reactQuery.useMutation({
    ...restOptions,
    mutationFn: (data) => client.templates.createTemplate(data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.templates.all });
      onSuccess?.(...args);
    }
  });
}
function useUpdateTemplate(options) {
  const { onSuccess, ...restOptions } = options ?? {};
  const client = useWipClient();
  const queryClient = reactQuery.useQueryClient();
  return reactQuery.useMutation({
    ...restOptions,
    mutationFn: ({ id, data }) => client.templates.updateTemplate(id, data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.templates.all });
      onSuccess?.(...args);
    }
  });
}
function useDeleteTemplate(options) {
  const { onSuccess, ...restOptions } = options ?? {};
  const client = useWipClient();
  const queryClient = reactQuery.useQueryClient();
  return reactQuery.useMutation({
    ...restOptions,
    mutationFn: ({ id, ...opts }) => client.templates.deleteTemplate(id, opts),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.templates.all });
      onSuccess?.(...args);
    }
  });
}
function useActivateTemplate(options) {
  const { onSuccess, ...restOptions } = options ?? {};
  const client = useWipClient();
  const queryClient = reactQuery.useQueryClient();
  return reactQuery.useMutation({
    ...restOptions,
    mutationFn: ({ id, ...opts }) => client.templates.activateTemplate(id, opts),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.templates.all });
      onSuccess?.(...args);
    }
  });
}
function useReactivateTemplate(options) {
  const { onSuccess, ...restOptions } = options ?? {};
  const client = useWipClient();
  const queryClient = reactQuery.useQueryClient();
  return reactQuery.useMutation({
    ...restOptions,
    mutationFn: ({ id, version, ...opts }) => client.templates.reactivateTemplate(id, version, opts),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.templates.all });
      onSuccess?.(...args);
    }
  });
}
function useAddEdgeTypeEndpoints(options) {
  const { onSuccess, ...restOptions } = options ?? {};
  const client = useWipClient();
  const queryClient = reactQuery.useQueryClient();
  return reactQuery.useMutation({
    ...restOptions,
    mutationFn: ({ id, ...opts }) => client.templates.addEdgeTypeEndpoints(id, opts),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.templates.all });
      onSuccess?.(...args);
    }
  });
}
function useCreateDocument(options) {
  const { onSuccess, ...restOptions } = options ?? {};
  const client = useWipClient();
  const queryClient = reactQuery.useQueryClient();
  return reactQuery.useMutation({
    ...restOptions,
    mutationFn: (data) => client.documents.createDocument(data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.documents.all });
      onSuccess?.(...args);
    }
  });
}
function useCreateDocuments(options) {
  const { onSuccess, ...restOptions } = options ?? {};
  const client = useWipClient();
  const queryClient = reactQuery.useQueryClient();
  return reactQuery.useMutation({
    ...restOptions,
    mutationFn: (data) => client.documents.createDocuments(data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.documents.all });
      onSuccess?.(...args);
    }
  });
}
function useUpdateDocument(options) {
  const { onSuccess, ...restOptions } = options ?? {};
  const client = useWipClient();
  const queryClient = reactQuery.useQueryClient();
  return reactQuery.useMutation({
    ...restOptions,
    mutationFn: ({ documentId, patch, ifMatch }) => client.documents.updateDocument(documentId, patch, { ifMatch }),
    onSuccess: (...args) => {
      const variables = args[1];
      queryClient.invalidateQueries({ queryKey: wipKeys.documents.detail(variables.documentId) });
      queryClient.invalidateQueries({ queryKey: wipKeys.documents.versions(variables.documentId) });
      queryClient.invalidateQueries({ queryKey: wipKeys.documents.all });
      onSuccess?.(...args);
    }
  });
}
function useUpdateDocuments(options) {
  const { onSuccess, ...restOptions } = options ?? {};
  const client = useWipClient();
  const queryClient = reactQuery.useQueryClient();
  return reactQuery.useMutation({
    ...restOptions,
    mutationFn: (items) => client.documents.updateDocuments(items),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.documents.all });
      onSuccess?.(...args);
    }
  });
}
function useDeleteDocument(options) {
  const { onSuccess, ...restOptions } = options ?? {};
  const client = useWipClient();
  const queryClient = reactQuery.useQueryClient();
  return reactQuery.useMutation({
    ...restOptions,
    mutationFn: ({ id, updatedBy }) => client.documents.deleteDocument(id, { updatedBy }),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.documents.all });
      onSuccess?.(...args);
    }
  });
}
function useArchiveDocument(options) {
  const { onSuccess, ...restOptions } = options ?? {};
  const client = useWipClient();
  const queryClient = reactQuery.useQueryClient();
  return reactQuery.useMutation({
    ...restOptions,
    mutationFn: ({ id, archivedBy }) => client.documents.archiveDocument(id, archivedBy),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.documents.all });
      onSuccess?.(...args);
    }
  });
}
function useUploadFile(options) {
  const { onSuccess, ...restOptions } = options ?? {};
  const client = useWipClient();
  const queryClient = reactQuery.useQueryClient();
  return reactQuery.useMutation({
    ...restOptions,
    mutationFn: ({ file, filename, metadata, namespace }) => client.files.uploadFile(file, filename, metadata, namespace),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.files.all });
      onSuccess?.(...args);
    }
  });
}
function useUpdateFileMetadata(options) {
  const { onSuccess, ...restOptions } = options ?? {};
  const client = useWipClient();
  const queryClient = reactQuery.useQueryClient();
  return reactQuery.useMutation({
    ...restOptions,
    mutationFn: ({ fileId, data }) => client.files.updateMetadata(fileId, data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.files.all });
      onSuccess?.(...args);
    }
  });
}
function useDeleteFile(options) {
  const { onSuccess, ...restOptions } = options ?? {};
  const client = useWipClient();
  const queryClient = reactQuery.useQueryClient();
  return reactQuery.useMutation({
    ...restOptions,
    mutationFn: (fileId) => client.files.deleteFile(fileId),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.files.all });
      onSuccess?.(...args);
    }
  });
}
function useDeleteFiles(options) {
  const { onSuccess, ...restOptions } = options ?? {};
  const client = useWipClient();
  const queryClient = reactQuery.useQueryClient();
  return reactQuery.useMutation({
    ...restOptions,
    mutationFn: (fileIds) => client.files.deleteFiles(fileIds),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.files.all });
      onSuccess?.(...args);
    }
  });
}
function useHardDeleteFile(options) {
  const { onSuccess, ...restOptions } = options ?? {};
  const client = useWipClient();
  const queryClient = reactQuery.useQueryClient();
  return reactQuery.useMutation({
    ...restOptions,
    mutationFn: (fileId) => client.files.hardDeleteFile(fileId),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.files.all });
      onSuccess?.(...args);
    }
  });
}
function useCreateTermRelations(options) {
  const { onSuccess, ...restOptions } = options ?? {};
  const client = useWipClient();
  const queryClient = reactQuery.useQueryClient();
  return reactQuery.useMutation({
    ...restOptions,
    mutationFn: ({ items, namespace }) => client.defStore.createTermRelations(items, namespace),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.terms.all });
      onSuccess?.(...args);
    }
  });
}
function useDeleteTermRelations(options) {
  const { onSuccess, ...restOptions } = options ?? {};
  const client = useWipClient();
  const queryClient = reactQuery.useQueryClient();
  return reactQuery.useMutation({
    ...restOptions,
    mutationFn: ({ items, namespace }) => client.defStore.deleteTermRelations(items, namespace),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.terms.all });
      onSuccess?.(...args);
    }
  });
}
function useCreateNamespace(options) {
  const { onSuccess, ...restOptions } = options ?? {};
  const client = useWipClient();
  const queryClient = reactQuery.useQueryClient();
  return reactQuery.useMutation({
    ...restOptions,
    mutationFn: (data) => client.registry.createNamespace(data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.registry.all });
      onSuccess?.(...args);
    }
  });
}
function useUpdateNamespace(options) {
  const { onSuccess, ...restOptions } = options ?? {};
  const client = useWipClient();
  const queryClient = reactQuery.useQueryClient();
  return reactQuery.useMutation({
    ...restOptions,
    mutationFn: ({ prefix, data }) => client.registry.updateNamespace(prefix, data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.registry.all });
      onSuccess?.(...args);
    }
  });
}
function useArchiveNamespace(options) {
  const { onSuccess, ...restOptions } = options ?? {};
  const client = useWipClient();
  const queryClient = reactQuery.useQueryClient();
  return reactQuery.useMutation({
    ...restOptions,
    mutationFn: ({ prefix, archivedBy }) => client.registry.archiveNamespace(prefix, archivedBy),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.registry.all });
      onSuccess?.(...args);
    }
  });
}
function useRestoreNamespace(options) {
  const { onSuccess, ...restOptions } = options ?? {};
  const client = useWipClient();
  const queryClient = reactQuery.useQueryClient();
  return reactQuery.useMutation({
    ...restOptions,
    mutationFn: ({ prefix, restoredBy }) => client.registry.restoreNamespace(prefix, restoredBy),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.registry.all });
      onSuccess?.(...args);
    }
  });
}
function useDeleteNamespace(options) {
  const { onSuccess, ...restOptions } = options ?? {};
  const client = useWipClient();
  const queryClient = reactQuery.useQueryClient();
  return reactQuery.useMutation({
    ...restOptions,
    mutationFn: ({ prefix, deletedBy }) => client.registry.deleteNamespace(prefix, deletedBy),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.registry.all });
      onSuccess?.(...args);
    }
  });
}
function useAddSynonym(options) {
  const { onSuccess, ...restOptions } = options ?? {};
  const client = useWipClient();
  const queryClient = reactQuery.useQueryClient();
  return reactQuery.useMutation({
    ...restOptions,
    mutationFn: (data) => client.registry.addSynonym(data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.registry.all });
      onSuccess?.(...args);
    }
  });
}
function useRemoveSynonym(options) {
  const { onSuccess, ...restOptions } = options ?? {};
  const client = useWipClient();
  const queryClient = reactQuery.useQueryClient();
  return reactQuery.useMutation({
    ...restOptions,
    mutationFn: (data) => client.registry.removeSynonym(data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.registry.all });
      onSuccess?.(...args);
    }
  });
}
function useMergeEntries(options) {
  const { onSuccess, ...restOptions } = options ?? {};
  const client = useWipClient();
  const queryClient = reactQuery.useQueryClient();
  return reactQuery.useMutation({
    ...restOptions,
    mutationFn: (data) => client.registry.mergeEntries(data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.registry.all });
      onSuccess?.(...args);
    }
  });
}
function useDeactivateEntry(options) {
  const { onSuccess, ...restOptions } = options ?? {};
  const client = useWipClient();
  const queryClient = reactQuery.useQueryClient();
  return reactQuery.useMutation({
    ...restOptions,
    mutationFn: ({ entryId, updatedBy }) => client.registry.deactivateEntry(entryId, updatedBy),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.registry.all });
      onSuccess?.(...args);
    }
  });
}
function useFormSchema(templateValue, options) {
  const client$1 = useWipClient();
  return reactQuery.useQuery({
    queryKey: [...wipKeys.templates.byValue(templateValue), "form-schema"],
    queryFn: async () => {
      const template = await client$1.templates.getTemplateByValue(templateValue);
      return client.templateToFormSchema(template);
    },
    staleTime: STALE_TIMES.templates,
    enabled: !!templateValue,
    ...options
  });
}
function useBulkImport(options) {
  const [progress, setProgress] = react.useState(null);
  const queryClient = reactQuery.useQueryClient();
  const mutation = reactQuery.useMutation({
    mutationFn: (items) => client.bulkImport(items, options.writeFn, {
      batchSize: options.batchSize,
      continueOnError: options.continueOnError,
      onProgress: setProgress
    }),
    onSuccess: () => {
      const keys = options.invalidateKeys ?? wipKeys.all;
      queryClient.invalidateQueries({ queryKey: keys });
    },
    onSettled: () => {
      setTimeout(() => setProgress(null), 2e3);
    }
  });
  const reset = react.useCallback(() => {
    setProgress(null);
    mutation.reset();
  }, [mutation]);
  return {
    ...mutation,
    progress,
    reset
  };
}

exports.STALE_TIMES = STALE_TIMES;
exports.WipFooter = WipFooter;
exports.WipProvider = WipProvider;
exports.useActivateTemplate = useActivateTemplate;
exports.useActivity = useActivity;
exports.useAddEdgeTypeEndpoints = useAddEdgeTypeEndpoints;
exports.useAddSynonym = useAddSynonym;
exports.useArchiveDocument = useArchiveDocument;
exports.useArchiveNamespace = useArchiveNamespace;
exports.useBatchJob = useBatchJob;
exports.useBatchJobs = useBatchJobs;
exports.useBulkImport = useBulkImport;
exports.useCancelBatchJob = useCancelBatchJob;
exports.useClearCompletedJobs = useClearCompletedJobs;
exports.useCreateDocument = useCreateDocument;
exports.useCreateDocuments = useCreateDocuments;
exports.useCreateNamespace = useCreateNamespace;
exports.useCreateTemplate = useCreateTemplate;
exports.useCreateTerm = useCreateTerm;
exports.useCreateTermRelations = useCreateTermRelations;
exports.useCreateTerminology = useCreateTerminology;
exports.useDeactivateEntry = useDeactivateEntry;
exports.useDeleteDocument = useDeleteDocument;
exports.useDeleteFile = useDeleteFile;
exports.useDeleteFiles = useDeleteFiles;
exports.useDeleteNamespace = useDeleteNamespace;
exports.useDeleteTemplate = useDeleteTemplate;
exports.useDeleteTerm = useDeleteTerm;
exports.useDeleteTermRelations = useDeleteTermRelations;
exports.useDeleteTerminology = useDeleteTerminology;
exports.useDeprecateTerm = useDeprecateTerm;
exports.useDocument = useDocument;
exports.useDocumentRelationships = useDocumentRelationships;
exports.useDocumentVersions = useDocumentVersions;
exports.useDocuments = useDocuments;
exports.useDownloadUrl = useDownloadUrl;
exports.useFile = useFile;
exports.useFiles = useFiles;
exports.useFormSchema = useFormSchema;
exports.useHardDeleteFile = useHardDeleteFile;
exports.useIntegrityCheck = useIntegrityCheck;
exports.useMergeEntries = useMergeEntries;
exports.useNamespaces = useNamespaces;
exports.useQueryDocuments = useQueryDocuments;
exports.useReactivateTemplate = useReactivateTemplate;
exports.useRegistrySearch = useRegistrySearch;
exports.useRemoveSynonym = useRemoveSynonym;
exports.useReportQuery = useReportQuery;
exports.useRestoreNamespace = useRestoreNamespace;
exports.useSyncStatus = useSyncStatus;
exports.useTemplate = useTemplate;
exports.useTemplateByValue = useTemplateByValue;
exports.useTemplates = useTemplates;
exports.useTerm = useTerm;
exports.useTerminologies = useTerminologies;
exports.useTerminology = useTerminology;
exports.useTerms = useTerms;
exports.useTraverseDocuments = useTraverseDocuments;
exports.useTriggerBatchSync = useTriggerBatchSync;
exports.useTriggerBatchSyncAll = useTriggerBatchSyncAll;
exports.useTriggerTermRelationSync = useTriggerTermRelationSync;
exports.useTriggerTermSync = useTriggerTermSync;
exports.useTriggerTerminologySync = useTriggerTerminologySync;
exports.useUpdateDocument = useUpdateDocument;
exports.useUpdateDocuments = useUpdateDocuments;
exports.useUpdateFileMetadata = useUpdateFileMetadata;
exports.useUpdateNamespace = useUpdateNamespace;
exports.useUpdateTemplate = useUpdateTemplate;
exports.useUpdateTerm = useUpdateTerm;
exports.useUpdateTerminology = useUpdateTerminology;
exports.useUploadFile = useUploadFile;
exports.useWipClient = useWipClient;
exports.wipKeys = wipKeys;
//# sourceMappingURL=index.cjs.map
//# sourceMappingURL=index.cjs.map