import { useMutation, useQueryClient } from '@tanstack/react-query'
import type { QueryClient, UseMutationOptions } from '@tanstack/react-query'
import type {
  NamespaceDeletionResult,
  SynonymResult,
  MergeResult,
  DeactivateEntryResult,
  BulkResultItem,
  BulkResponse,
  CreateTerminologyRequest,
  UpdateTerminologyRequest,
  CreateTermRequest,
  UpdateTermRequest,
  DeprecateTermRequest,
  CreateTemplateRequest,
  UpdateTemplateRequest,
  ActivateTemplateResponse,
  Template,
  CreateDocumentRequest,
  PatchDocumentRequest,
  FileUploadMetadata,
  FileEntity,
  UpdateFileMetadataRequest,
  CreateTermRelationRequest,
  DeleteTermRelationRequest,
  CreateNamespaceRequest,
  UpdateNamespaceRequest,
  Namespace,
  AddSynonymRequest,
  RemoveSynonymRequest,
  MergeRequest,
  RehashIdentitiesRequest,
  RehashIdentitiesResponse,
} from '@wip/client'
import { useWipClient } from '../provider.js'
import { wipKeys } from '../utils/keys.js'

/**
 * A document write moves the reference count and status of the files it
 * references or stops referencing, so file lists and details refetch. Download
 * URLs are not touched (a refetch would mint new ones for nothing).
 */
function invalidateFileViews(queryClient: QueryClient): void {
  queryClient.invalidateQueries({ queryKey: [...wipKeys.files.all, 'list'] })
  queryClient.invalidateQueries({ queryKey: [...wipKeys.files.all, 'detail'] })
}

/**
 * A created entity gets a Registry entry, so Registry listings and searches
 * on screen refetch; ones not mounted are only marked stale.
 */
function invalidateRegistryListings(queryClient: QueryClient): void {
  queryClient.invalidateQueries({ queryKey: wipKeys.registry.all, refetchType: 'active' })
}

// ============================================================================
// TERMINOLOGY HOOKS
// ============================================================================

export function useCreateTerminology(
  options?: Omit<UseMutationOptions<BulkResultItem, Error, CreateTerminologyRequest>, 'mutationFn'>,
) {
  const { onSuccess, ...restOptions } = options ?? {}
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    ...restOptions,
    mutationFn: (data: CreateTerminologyRequest) => client.defStore.createTerminology(data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.terminologies.all })
      invalidateRegistryListings(queryClient)
      onSuccess?.(...args)
    },
  })
}

export function useUpdateTerminology(
  options?: Omit<UseMutationOptions<BulkResultItem, Error, { id: string; data: UpdateTerminologyRequest }>, 'mutationFn'>,
) {
  const { onSuccess, ...restOptions } = options ?? {}
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    ...restOptions,
    mutationFn: ({ id, data }: { id: string; data: UpdateTerminologyRequest }) =>
      client.defStore.updateTerminology(id, data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.terminologies.all })
      onSuccess?.(...args)
    },
  })
}

export function useDeleteTerminology(
  options?: Omit<UseMutationOptions<BulkResultItem, Error, string>, 'mutationFn'>,
) {
  const { onSuccess, ...restOptions } = options ?? {}
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    ...restOptions,
    mutationFn: (id: string) => client.defStore.deleteTerminology(id),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.terminologies.all })
      onSuccess?.(...args)
    },
  })
}

// ============================================================================
// TERM HOOKS
// ============================================================================

export function useCreateTerm(
  terminologyId: string,
  namespace: string,
  options?: Omit<UseMutationOptions<BulkResultItem, Error, CreateTermRequest>, 'mutationFn'>,
) {
  const { onSuccess, ...restOptions } = options ?? {}
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    ...restOptions,
    mutationFn: (data: CreateTermRequest) => client.defStore.createTerm(terminologyId, data, { namespace }),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.terms.all })
      queryClient.invalidateQueries({ queryKey: wipKeys.terminologies.detail(terminologyId) })
      invalidateRegistryListings(queryClient)
      onSuccess?.(...args)
    },
  })
}

export function useUpdateTerm(
  options?: Omit<UseMutationOptions<BulkResultItem, Error, { termId: string; data: UpdateTermRequest }>, 'mutationFn'>,
) {
  const { onSuccess, ...restOptions } = options ?? {}
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    ...restOptions,
    mutationFn: ({ termId, data }: { termId: string; data: UpdateTermRequest }) =>
      client.defStore.updateTerm(termId, data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.terms.all })
      onSuccess?.(...args)
    },
  })
}

export function useDeprecateTerm(
  options?: Omit<UseMutationOptions<BulkResultItem, Error, { termId: string; data: DeprecateTermRequest }>, 'mutationFn'>,
) {
  const { onSuccess, ...restOptions } = options ?? {}
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    ...restOptions,
    mutationFn: ({ termId, data }: { termId: string; data: DeprecateTermRequest }) =>
      client.defStore.deprecateTerm(termId, data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.terms.all })
      onSuccess?.(...args)
    },
  })
}

export function useDeleteTerm(
  terminologyId: string,
  options?: Omit<UseMutationOptions<BulkResultItem, Error, string>, 'mutationFn'>,
) {
  const { onSuccess, ...restOptions } = options ?? {}
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    ...restOptions,
    mutationFn: (termId: string) => client.defStore.deleteTerm(termId),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.terms.all })
      queryClient.invalidateQueries({ queryKey: wipKeys.terminologies.detail(terminologyId) })
      onSuccess?.(...args)
    },
  })
}

// ============================================================================
// TEMPLATE HOOKS
// ============================================================================

export function useCreateTemplate(
  options?: Omit<UseMutationOptions<BulkResultItem, Error, CreateTemplateRequest>, 'mutationFn'>,
) {
  const { onSuccess, ...restOptions } = options ?? {}
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    ...restOptions,
    mutationFn: (data: CreateTemplateRequest) => client.templates.createTemplate(data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.templates.all })
      invalidateRegistryListings(queryClient)
      onSuccess?.(...args)
    },
  })
}

export function useUpdateTemplate(
  options?: Omit<UseMutationOptions<BulkResultItem, Error, { id: string; data: UpdateTemplateRequest }>, 'mutationFn'>,
) {
  const { onSuccess, ...restOptions } = options ?? {}
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    ...restOptions,
    mutationFn: ({ id, data }: { id: string; data: UpdateTemplateRequest }) =>
      client.templates.updateTemplate(id, data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.templates.all })
      onSuccess?.(...args)
    },
  })
}

export function useDeleteTemplate(
  options?: Omit<UseMutationOptions<BulkResultItem, Error, { id: string; updatedBy?: string; version?: number; force?: boolean; hardDelete?: boolean }>, 'mutationFn'>,
) {
  const { onSuccess, ...restOptions } = options ?? {}
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    ...restOptions,
    mutationFn: ({ id, ...opts }: { id: string; updatedBy?: string; version?: number; force?: boolean; hardDelete?: boolean }) =>
      client.templates.deleteTemplate(id, opts),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.templates.all })
      onSuccess?.(...args)
    },
  })
}

export function useActivateTemplate(
  options?: Omit<UseMutationOptions<ActivateTemplateResponse, Error, { id: string; namespace: string; dry_run?: boolean }>, 'mutationFn'>,
) {
  const { onSuccess, ...restOptions } = options ?? {}
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    ...restOptions,
    mutationFn: ({ id, ...opts }: { id: string; namespace: string; dry_run?: boolean }) =>
      client.templates.activateTemplate(id, opts),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.templates.all })
      onSuccess?.(...args)
    },
  })
}

export function useReactivateTemplate(
  options?: Omit<UseMutationOptions<Template, Error, { id: string; version: number; namespace: string }>, 'mutationFn'>,
) {
  const { onSuccess, ...restOptions } = options ?? {}
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    ...restOptions,
    mutationFn: ({ id, version, ...opts }: { id: string; version: number; namespace: string }) =>
      client.templates.reactivateTemplate(id, version, opts),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.templates.all })
      onSuccess?.(...args)
    },
  })
}

export function useAddEdgeTypeEndpoints(
  options?: Omit<
    UseMutationOptions<
      Template,
      Error,
      { id: string; namespace: string; addSourceTemplates?: string[]; addTargetTemplates?: string[] }
    >,
    'mutationFn'
  >,
) {
  const { onSuccess, ...restOptions } = options ?? {}
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    ...restOptions,
    mutationFn: ({ id, ...opts }: { id: string; namespace: string; addSourceTemplates?: string[]; addTargetTemplates?: string[] }) =>
      client.templates.addEdgeTypeEndpoints(id, opts),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.templates.all })
      onSuccess?.(...args)
    },
  })
}

// ============================================================================
// DOCUMENT HOOKS
// ============================================================================

export function useCreateDocument(
  options?: Omit<UseMutationOptions<BulkResultItem, Error, CreateDocumentRequest>, 'mutationFn'>,
) {
  const { onSuccess, ...restOptions } = options ?? {}
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    ...restOptions,
    mutationFn: (data: CreateDocumentRequest) => client.documents.createDocument(data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.documents.all })
      invalidateFileViews(queryClient)
      invalidateRegistryListings(queryClient)
      onSuccess?.(...args)
    },
  })
}

export function useCreateDocuments(
  options?: Omit<UseMutationOptions<BulkResponse, Error, CreateDocumentRequest[]>, 'mutationFn'>,
) {
  const { onSuccess, ...restOptions } = options ?? {}
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    ...restOptions,
    mutationFn: (data: CreateDocumentRequest[]) => client.documents.createDocuments(data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.documents.all })
      invalidateFileViews(queryClient)
      invalidateRegistryListings(queryClient)
      onSuccess?.(...args)
    },
  })
}

/**
 * Apply an RFC 7396 JSON Merge Patch to a document.
 *
 * Wraps `client.documents.updateDocument`. Throws `WipBulkItemError`
 * (with `errorCode` populated) on per-item failure. Invalidates both
 * the document detail and the documents list on success.
 */
export function useUpdateDocument(
  options?: Omit<
    UseMutationOptions<
      BulkResultItem,
      Error,
      {
        documentId: string
        patch: Record<string, unknown>
        ifMatch?: number
        /**
         * RFC 7396 merge patch for `metadata.custom`. Metadata versions
         * like data (a metadata-only change mints a new version); pass
         * `patch: {}` for a metadata-only update.
         */
        metadataPatch?: Record<string, unknown>
      }
    >,
    'mutationFn'
  >,
) {
  const { onSuccess, ...restOptions } = options ?? {}
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    ...restOptions,
    mutationFn: ({ documentId, patch, ifMatch, metadataPatch }) =>
      client.documents.updateDocument(documentId, patch, { ifMatch, metadataPatch }),
    onSuccess: (...args) => {
      const variables = args[1]
      queryClient.invalidateQueries({ queryKey: wipKeys.documents.detail(variables.documentId) })
      queryClient.invalidateQueries({ queryKey: wipKeys.documents.versions(variables.documentId) })
      queryClient.invalidateQueries({ queryKey: wipKeys.documents.all })
      invalidateFileViews(queryClient)
      onSuccess?.(...args)
    },
  })
}

/**
 * Bulk variant of {@link useUpdateDocument}. Returns the raw `BulkResponse`
 * so the caller can inspect per-item `error_code` values without per-item
 * exceptions interrupting the batch.
 */
export function useUpdateDocuments(
  options?: Omit<UseMutationOptions<BulkResponse, Error, PatchDocumentRequest[]>, 'mutationFn'>,
) {
  const { onSuccess, ...restOptions } = options ?? {}
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    ...restOptions,
    mutationFn: (items: PatchDocumentRequest[]) => client.documents.updateDocuments(items),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.documents.all })
      invalidateFileViews(queryClient)
      onSuccess?.(...args)
    },
  })
}

export function useDeleteDocument(
  options?: Omit<UseMutationOptions<BulkResultItem, Error, { id: string; updatedBy?: string }>, 'mutationFn'>,
) {
  const { onSuccess, ...restOptions } = options ?? {}
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    ...restOptions,
    mutationFn: ({ id, updatedBy }) => client.documents.deleteDocument(id, { updatedBy }),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.documents.all })
      invalidateFileViews(queryClient)
      onSuccess?.(...args)
    },
  })
}

// ============================================================================
// FILE HOOKS
// ============================================================================

/** A file id, or the id with the namespace resolving it and `force` (delete although documents reference it). */
export interface DeleteFileVariables {
  fileId: string
  namespace?: string
  force?: boolean
}

export interface DeleteFilesVariables {
  fileIds: string[]
  namespace?: string
  force?: boolean
}

export function useUploadFile(
  options?: Omit<
    UseMutationOptions<
      FileEntity,
      Error,
      // namespace is required: the service refuses an upload without one.
      { file: File | Blob; filename?: string; metadata?: FileUploadMetadata; namespace: string }
    >,
    'mutationFn'
  >,
) {
  const { onSuccess, ...restOptions } = options ?? {}
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    ...restOptions,
    mutationFn: ({ file, filename, metadata, namespace }) =>
      client.files.uploadFile(file, filename, metadata, namespace),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.files.all })
      invalidateRegistryListings(queryClient)
      onSuccess?.(...args)
    },
  })
}

export function useUpdateFileMetadata(
  options?: Omit<UseMutationOptions<BulkResultItem, Error, { fileId: string; data: UpdateFileMetadataRequest }>, 'mutationFn'>,
) {
  const { onSuccess, ...restOptions } = options ?? {}
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    ...restOptions,
    mutationFn: ({ fileId, data }: { fileId: string; data: UpdateFileMetadataRequest }) =>
      client.files.updateMetadata(fileId, data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.files.all })
      onSuccess?.(...args)
    },
  })
}

export function useDeleteFile(
  options?: Omit<UseMutationOptions<BulkResultItem, Error, string | DeleteFileVariables>, 'mutationFn'>,
) {
  const { onSuccess, ...restOptions } = options ?? {}
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    ...restOptions,
    mutationFn: (vars: string | DeleteFileVariables) => {
      const { fileId, namespace, force } = typeof vars === 'string' ? { fileId: vars } : vars
      return client.files.deleteFile(fileId, namespace, { force })
    },
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.files.all })
      onSuccess?.(...args)
    },
  })
}

export function useDeleteFiles(
  options?: Omit<UseMutationOptions<BulkResponse, Error, string[] | DeleteFilesVariables>, 'mutationFn'>,
) {
  const { onSuccess, ...restOptions } = options ?? {}
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    ...restOptions,
    mutationFn: (vars: string[] | DeleteFilesVariables) => {
      const { fileIds, namespace, force } = Array.isArray(vars) ? { fileIds: vars } : vars
      return client.files.deleteFiles(fileIds, namespace, { force })
    },
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.files.all })
      onSuccess?.(...args)
    },
  })
}

export function useHardDeleteFile(
  options?: Omit<UseMutationOptions<void, Error, string>, 'mutationFn'>,
) {
  const { onSuccess, ...restOptions } = options ?? {}
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    ...restOptions,
    mutationFn: (fileId: string) => client.files.hardDeleteFile(fileId),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.files.all })
      onSuccess?.(...args)
    },
  })
}

// ============================================================================
// ONTOLOGY / TERM-RELATION HOOKS
// ============================================================================
// The platform renamed this surface in WIP commit 2eeb872 (Phase 0 of
// the document-relationships work, 2026-04-25): "relationship" now
// refers to document-to-document edges; "term-relation" refers to the
// term-ontology edges (is_a, part_of, ...). @wip/client@0.13.0 carried
// the rename; @wip/react was the trailing gap (CASE-167) and follows
// it here. No backward-compat aliases — fresh-instance restart is the
// recovery path.

export function useCreateTermRelations(
  options?: Omit<UseMutationOptions<BulkResponse, Error, { items: CreateTermRelationRequest[]; namespace: string }>, 'mutationFn'>,
) {
  const { onSuccess, ...restOptions } = options ?? {}
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    ...restOptions,
    mutationFn: ({ items, namespace }: { items: CreateTermRelationRequest[]; namespace: string }) =>
      client.defStore.createTermRelations(items, namespace),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.terms.all })
      onSuccess?.(...args)
    },
  })
}

export function useDeleteTermRelations(
  options?: Omit<UseMutationOptions<BulkResponse, Error, { items: DeleteTermRelationRequest[]; namespace: string }>, 'mutationFn'>,
) {
  const { onSuccess, ...restOptions } = options ?? {}
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    ...restOptions,
    mutationFn: ({ items, namespace }: { items: DeleteTermRelationRequest[]; namespace: string }) =>
      client.defStore.deleteTermRelations(items, namespace),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.terms.all })
      onSuccess?.(...args)
    },
  })
}

// ============================================================================
// NAMESPACE HOOKS
// ============================================================================

export function useCreateNamespace(
  options?: Omit<UseMutationOptions<Namespace, Error, CreateNamespaceRequest>, 'mutationFn'>,
) {
  const { onSuccess, ...restOptions } = options ?? {}
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    ...restOptions,
    mutationFn: (data: CreateNamespaceRequest) => client.registry.createNamespace(data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.registry.all })
      onSuccess?.(...args)
    },
  })
}

export function useUpdateNamespace(
  options?: Omit<UseMutationOptions<Namespace, Error, { prefix: string; data: UpdateNamespaceRequest }>, 'mutationFn'>,
) {
  const { onSuccess, ...restOptions } = options ?? {}
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    ...restOptions,
    mutationFn: ({ prefix, data }: { prefix: string; data: UpdateNamespaceRequest }) =>
      client.registry.updateNamespace(prefix, data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.registry.all })
      onSuccess?.(...args)
    },
  })
}

export function useArchiveNamespace(
  options?: Omit<UseMutationOptions<Namespace, Error, { prefix: string; archivedBy?: string }>, 'mutationFn'>,
) {
  const { onSuccess, ...restOptions } = options ?? {}
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    ...restOptions,
    mutationFn: ({ prefix, archivedBy }: { prefix: string; archivedBy?: string }) =>
      client.registry.archiveNamespace(prefix, archivedBy),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.registry.all })
      onSuccess?.(...args)
    },
  })
}

export function useRestoreNamespace(
  options?: Omit<UseMutationOptions<Namespace, Error, { prefix: string; restoredBy?: string }>, 'mutationFn'>,
) {
  const { onSuccess, ...restOptions } = options ?? {}
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    ...restOptions,
    mutationFn: ({ prefix, restoredBy }: { prefix: string; restoredBy?: string }) =>
      client.registry.restoreNamespace(prefix, restoredBy),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.registry.all })
      onSuccess?.(...args)
    },
  })
}

/**
 * Resolves with the deletion's status: `in_progress` means it is not finished
 * (a step was deferred), so read `status` rather than treating success as done.
 */
export function useDeleteNamespace(
  options?: Omit<
    UseMutationOptions<NamespaceDeletionResult, Error, { prefix: string; deletedBy?: string; force?: boolean }>,
    'mutationFn'
  >,
) {
  const { onSuccess, ...restOptions } = options ?? {}
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    ...restOptions,
    mutationFn: ({ prefix, deletedBy, force }: { prefix: string; deletedBy?: string; force?: boolean }) =>
      client.registry.deleteNamespace(prefix, deletedBy, { force }),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.registry.all })
      onSuccess?.(...args)
    },
  })
}

// ============================================================================
// REGISTRY ENTRY HOOKS (synonyms, merge, deactivate)
// ============================================================================

export function useAddSynonym(
  options?: Omit<UseMutationOptions<SynonymResult, Error, AddSynonymRequest>, 'mutationFn'>,
) {
  const { onSuccess, ...restOptions } = options ?? {}
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    ...restOptions,
    mutationFn: (data: AddSynonymRequest) => client.registry.addSynonym(data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.registry.all })
      onSuccess?.(...args)
    },
  })
}

export function useRemoveSynonym(
  options?: Omit<UseMutationOptions<SynonymResult, Error, RemoveSynonymRequest>, 'mutationFn'>,
) {
  const { onSuccess, ...restOptions } = options ?? {}
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    ...restOptions,
    mutationFn: (data: RemoveSynonymRequest) => client.registry.removeSynonym(data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.registry.all })
      onSuccess?.(...args)
    },
  })
}

export function useMergeEntries(
  options?: Omit<UseMutationOptions<MergeResult, Error, MergeRequest>, 'mutationFn'>,
) {
  const { onSuccess, ...restOptions } = options ?? {}
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    ...restOptions,
    mutationFn: (data: MergeRequest) => client.registry.mergeEntries(data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.registry.all })
      onSuccess?.(...args)
    },
  })
}

export function useDeactivateEntry(
  options?: Omit<UseMutationOptions<DeactivateEntryResult, Error, { entryId: string; updatedBy?: string }>, 'mutationFn'>,
) {
  const { onSuccess, ...restOptions } = options ?? {}
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    ...restOptions,
    mutationFn: ({ entryId, updatedBy }: { entryId: string; updatedBy?: string }) =>
      client.registry.deactivateEntry(entryId, updatedBy),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.registry.all })
      onSuccess?.(...args)
    },
  })
}

// ============================================================================
// MAINTENANCE HOOKS
// ============================================================================

// The pass defaults to a dry run on the server. Caches are invalidated only
// when the pass was applied: a dry run changes nothing.

export function useRehashIdentities(
  options?: Omit<UseMutationOptions<RehashIdentitiesResponse, Error, RehashIdentitiesRequest>, 'mutationFn'>,
) {
  const { onSuccess, ...restOptions } = options ?? {}
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    ...restOptions,
    mutationFn: (data: RehashIdentitiesRequest) => client.documents.rehashIdentities(data),
    onSuccess: (...args) => {
      if (!args[0].dry_run) {
        queryClient.invalidateQueries({ queryKey: wipKeys.documents.all })
        queryClient.invalidateQueries({ queryKey: wipKeys.registry.all })
      }
      onSuccess?.(...args)
    },
  })
}
