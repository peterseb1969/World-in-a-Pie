// Provider
export { WipProvider, useWipClient, type WipProviderProps } from './provider.js'

// App origins: keep the app's own session fresh (CASE-1061)
export {
  useAppSessionRenewal,
  APP_SESSION_RENEW_PATH,
  APP_SESSION_MESSAGE,
  type AppSessionOptions,
} from './app-session.js'

// Brand-attribution component (CASE-308)
export { WipFooter, type WipFooterProps } from './WipFooter.js'

// Query key factories
export { wipKeys } from './utils/keys.js'
export { STALE_TIMES } from './utils/defaults.js'

// Read hooks
export { useTerminologies, useTerminology } from './hooks/use-terminologies.js'
export { useTerms, useTerm } from './hooks/use-terms.js'
export { useTemplates, useTemplate, useTemplateByValue } from './hooks/use-templates.js'
export {
  useDocuments,
  useDocument,
  useQueryDocuments,
  useDocumentVersions,
  useTableView,
  // Phase-4 relationship-graph queries (CASE-296)
  useDocumentRelationships,
  useTraverseDocuments,
} from './hooks/use-documents.js'
export { useFiles, useFile, useDownloadUrl } from './hooks/use-files.js'
export { useNamespaces, useRegistrySearch } from './hooks/use-registry.js'
export {
  useReportQuery,
  useIntegrityCheck,
  useActivity,
  // Batch sync (CASE-283)
  useSyncStatus,
  useBatchJobs,
  useBatchJob,
  useTriggerBatchSyncAll,
  useTriggerBatchSync,
  useTriggerTerminologySync,
  useTriggerTermSync,
  useTriggerTermRelationSync,
  useCancelBatchJob,
  useClearCompletedJobs,
} from './hooks/use-reporting.js'

// Write hooks
export {
  // Terminologies
  useCreateTerminology,
  useUpdateTerminology,
  useDeleteTerminology,
  // Terms
  useCreateTerm,
  useUpdateTerm,
  useDeprecateTerm,
  useDeleteTerm,
  // Templates
  useCreateTemplate,
  useUpdateTemplate,
  useDeleteTemplate,
  useActivateTemplate,
  useReactivateTemplate,
  useAddEdgeTypeEndpoints,
  // Documents
  useCreateDocument,
  useCreateDocuments,
  useUpdateDocument,
  useUpdateDocuments,
  useDeleteDocument,
  // Files
  useUploadFile,
  useUpdateFileMetadata,
  useDeleteFile,
  useDeleteFiles,
  useHardDeleteFile,
  // Ontology / Term-Relations (renamed from "Relationships" in CASE-67/167)
  useCreateTermRelations,
  useDeleteTermRelations,
  // Namespaces
  useCreateNamespace,
  useUpdateNamespace,
  useArchiveNamespace,
  useRestoreNamespace,
  useDeleteNamespace,
  // Registry entries
  useAddSynonym,
  useRemoveSynonym,
  useMergeEntries,
  useDeactivateEntry,
  // Maintenance
  useRehashIdentities,
} from './hooks/use-mutations.js'

// Specialized hooks
export { useFormSchema } from './hooks/use-form-schema.js'
export { useBulkImport } from './hooks/use-bulk-import.js'
