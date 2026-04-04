// Provider
export { WipProvider, useWipClient, type WipProviderProps } from './provider.js'

// Query key factories
export { wipKeys } from './utils/keys.js'
export { STALE_TIMES } from './utils/defaults.js'

// Read hooks
export { useTerminologies, useTerminology } from './hooks/use-terminologies.js'
export { useTerms, useTerm } from './hooks/use-terms.js'
export { useTemplates, useTemplate, useTemplateByValue } from './hooks/use-templates.js'
export { useDocuments, useDocument, useQueryDocuments, useDocumentVersions } from './hooks/use-documents.js'
export { useFiles, useFile, useDownloadUrl } from './hooks/use-files.js'
export { useNamespaces, useRegistrySearch } from './hooks/use-registry.js'
export { useReportQuery, useIntegrityCheck, useActivity } from './hooks/use-reporting.js'

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
  // Documents
  useCreateDocument,
  useCreateDocuments,
  useDeleteDocument,
  useArchiveDocument,
  // Files
  useUploadFile,
  useUpdateFileMetadata,
  useDeleteFile,
  useDeleteFiles,
  useHardDeleteFile,
  // Ontology / Relationships
  useCreateRelationships,
  useDeleteRelationships,
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
} from './hooks/use-mutations.js'

// Specialized hooks
export { useFormSchema } from './hooks/use-form-schema.js'
export { useBulkImport } from './hooks/use-bulk-import.js'
