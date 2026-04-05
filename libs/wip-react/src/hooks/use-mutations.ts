import { useMutation, useQueryClient } from '@tanstack/react-query'
import type { UseMutationOptions } from '@tanstack/react-query'
import type {
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
  CreateDocumentRequest,
  FileUploadMetadata,
  FileEntity,
  UpdateFileMetadataRequest,
  CreateRelationshipRequest,
  DeleteRelationshipRequest,
  CreateNamespaceRequest,
  UpdateNamespaceRequest,
  Namespace,
  AddSynonymRequest,
  RemoveSynonymRequest,
  MergeRequest,
} from '@wip/client'
import { useWipClient } from '../provider.js'
import { wipKeys } from '../utils/keys.js'

// ============================================================================
// TERMINOLOGY HOOKS
// ============================================================================

export function useCreateTerminology(
  options?: Omit<UseMutationOptions<BulkResultItem, Error, CreateTerminologyRequest>, 'mutationFn'>,
) {
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (data: CreateTerminologyRequest) => client.defStore.createTerminology(data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.terminologies.all })
      options?.onSuccess?.(...args)
    },
    ...options,
  })
}

export function useUpdateTerminology(
  options?: Omit<UseMutationOptions<BulkResultItem, Error, { id: string; data: UpdateTerminologyRequest }>, 'mutationFn'>,
) {
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: UpdateTerminologyRequest }) =>
      client.defStore.updateTerminology(id, data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.terminologies.all })
      options?.onSuccess?.(...args)
    },
    ...options,
  })
}

export function useDeleteTerminology(
  options?: Omit<UseMutationOptions<BulkResultItem, Error, string>, 'mutationFn'>,
) {
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => client.defStore.deleteTerminology(id),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.terminologies.all })
      options?.onSuccess?.(...args)
    },
    ...options,
  })
}

// ============================================================================
// TERM HOOKS
// ============================================================================

export function useCreateTerm(
  terminologyId: string,
  options?: Omit<UseMutationOptions<BulkResultItem, Error, CreateTermRequest>, 'mutationFn'>,
) {
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (data: CreateTermRequest) => client.defStore.createTerm(terminologyId, data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.terms.all })
      queryClient.invalidateQueries({ queryKey: wipKeys.terminologies.detail(terminologyId) })
      options?.onSuccess?.(...args)
    },
    ...options,
  })
}

export function useUpdateTerm(
  options?: Omit<UseMutationOptions<BulkResultItem, Error, { termId: string; data: UpdateTermRequest }>, 'mutationFn'>,
) {
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ termId, data }: { termId: string; data: UpdateTermRequest }) =>
      client.defStore.updateTerm(termId, data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.terms.all })
      options?.onSuccess?.(...args)
    },
    ...options,
  })
}

export function useDeprecateTerm(
  options?: Omit<UseMutationOptions<BulkResultItem, Error, { termId: string; data: DeprecateTermRequest }>, 'mutationFn'>,
) {
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ termId, data }: { termId: string; data: DeprecateTermRequest }) =>
      client.defStore.deprecateTerm(termId, data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.terms.all })
      options?.onSuccess?.(...args)
    },
    ...options,
  })
}

export function useDeleteTerm(
  terminologyId: string,
  options?: Omit<UseMutationOptions<BulkResultItem, Error, string>, 'mutationFn'>,
) {
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (termId: string) => client.defStore.deleteTerm(termId),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.terms.all })
      queryClient.invalidateQueries({ queryKey: wipKeys.terminologies.detail(terminologyId) })
      options?.onSuccess?.(...args)
    },
    ...options,
  })
}

// ============================================================================
// TEMPLATE HOOKS
// ============================================================================

export function useCreateTemplate(
  options?: Omit<UseMutationOptions<BulkResultItem, Error, CreateTemplateRequest>, 'mutationFn'>,
) {
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (data: CreateTemplateRequest) => client.templates.createTemplate(data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.templates.all })
      options?.onSuccess?.(...args)
    },
    ...options,
  })
}

export function useUpdateTemplate(
  options?: Omit<UseMutationOptions<BulkResultItem, Error, { id: string; data: UpdateTemplateRequest }>, 'mutationFn'>,
) {
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: UpdateTemplateRequest }) =>
      client.templates.updateTemplate(id, data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.templates.all })
      options?.onSuccess?.(...args)
    },
    ...options,
  })
}

export function useDeleteTemplate(
  options?: Omit<UseMutationOptions<BulkResultItem, Error, { id: string; updatedBy?: string; version?: number; force?: boolean; hardDelete?: boolean }>, 'mutationFn'>,
) {
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, ...opts }: { id: string; updatedBy?: string; version?: number; force?: boolean; hardDelete?: boolean }) =>
      client.templates.deleteTemplate(id, opts),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.templates.all })
      options?.onSuccess?.(...args)
    },
    ...options,
  })
}

export function useActivateTemplate(
  options?: Omit<UseMutationOptions<ActivateTemplateResponse, Error, { id: string; namespace: string; dry_run?: boolean }>, 'mutationFn'>,
) {
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, ...opts }: { id: string; namespace: string; dry_run?: boolean }) =>
      client.templates.activateTemplate(id, opts),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.templates.all })
      options?.onSuccess?.(...args)
    },
    ...options,
  })
}

// ============================================================================
// DOCUMENT HOOKS
// ============================================================================

export function useCreateDocument(
  options?: Omit<UseMutationOptions<BulkResultItem, Error, CreateDocumentRequest>, 'mutationFn'>,
) {
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (data: CreateDocumentRequest) => client.documents.createDocument(data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.documents.all })
      options?.onSuccess?.(...args)
    },
    ...options,
  })
}

export function useCreateDocuments(
  options?: Omit<UseMutationOptions<BulkResponse, Error, CreateDocumentRequest[]>, 'mutationFn'>,
) {
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (data: CreateDocumentRequest[]) => client.documents.createDocuments(data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.documents.all })
      options?.onSuccess?.(...args)
    },
    ...options,
  })
}

export function useDeleteDocument(
  options?: Omit<UseMutationOptions<BulkResultItem, Error, { id: string; updatedBy?: string }>, 'mutationFn'>,
) {
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, updatedBy }) => client.documents.deleteDocument(id, { updatedBy }),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.documents.all })
      options?.onSuccess?.(...args)
    },
    ...options,
  })
}

export function useArchiveDocument(
  options?: Omit<UseMutationOptions<BulkResultItem, Error, { id: string; archivedBy?: string }>, 'mutationFn'>,
) {
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, archivedBy }) => client.documents.archiveDocument(id, archivedBy),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.documents.all })
      options?.onSuccess?.(...args)
    },
    ...options,
  })
}

// ============================================================================
// FILE HOOKS
// ============================================================================

export function useUploadFile(
  options?: Omit<UseMutationOptions<FileEntity, Error, { file: File | Blob; filename?: string; metadata?: FileUploadMetadata }>, 'mutationFn'>,
) {
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ file, filename, metadata }) => client.files.uploadFile(file, filename, metadata),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.files.all })
      options?.onSuccess?.(...args)
    },
    ...options,
  })
}

export function useUpdateFileMetadata(
  options?: Omit<UseMutationOptions<BulkResultItem, Error, { fileId: string; data: UpdateFileMetadataRequest }>, 'mutationFn'>,
) {
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ fileId, data }: { fileId: string; data: UpdateFileMetadataRequest }) =>
      client.files.updateMetadata(fileId, data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.files.all })
      options?.onSuccess?.(...args)
    },
    ...options,
  })
}

export function useDeleteFile(
  options?: Omit<UseMutationOptions<BulkResultItem, Error, string>, 'mutationFn'>,
) {
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (fileId: string) => client.files.deleteFile(fileId),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.files.all })
      options?.onSuccess?.(...args)
    },
    ...options,
  })
}

export function useDeleteFiles(
  options?: Omit<UseMutationOptions<BulkResponse, Error, string[]>, 'mutationFn'>,
) {
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (fileIds: string[]) => client.files.deleteFiles(fileIds),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.files.all })
      options?.onSuccess?.(...args)
    },
    ...options,
  })
}

export function useHardDeleteFile(
  options?: Omit<UseMutationOptions<void, Error, string>, 'mutationFn'>,
) {
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (fileId: string) => client.files.hardDeleteFile(fileId),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.files.all })
      options?.onSuccess?.(...args)
    },
    ...options,
  })
}

// ============================================================================
// ONTOLOGY / RELATIONSHIP HOOKS
// ============================================================================

export function useCreateRelationships(
  options?: Omit<UseMutationOptions<BulkResponse, Error, { items: CreateRelationshipRequest[]; namespace: string }>, 'mutationFn'>,
) {
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ items, namespace }: { items: CreateRelationshipRequest[]; namespace: string }) =>
      client.defStore.createRelationships(items, namespace),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.terms.all })
      options?.onSuccess?.(...args)
    },
    ...options,
  })
}

export function useDeleteRelationships(
  options?: Omit<UseMutationOptions<BulkResponse, Error, { items: DeleteRelationshipRequest[]; namespace: string }>, 'mutationFn'>,
) {
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ items, namespace }: { items: DeleteRelationshipRequest[]; namespace: string }) =>
      client.defStore.deleteRelationships(items, namespace),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.terms.all })
      options?.onSuccess?.(...args)
    },
    ...options,
  })
}

// ============================================================================
// NAMESPACE HOOKS
// ============================================================================

export function useCreateNamespace(
  options?: Omit<UseMutationOptions<Namespace, Error, CreateNamespaceRequest>, 'mutationFn'>,
) {
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (data: CreateNamespaceRequest) => client.registry.createNamespace(data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.registry.all })
      options?.onSuccess?.(...args)
    },
    ...options,
  })
}

export function useUpdateNamespace(
  options?: Omit<UseMutationOptions<Namespace, Error, { prefix: string; data: UpdateNamespaceRequest }>, 'mutationFn'>,
) {
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ prefix, data }: { prefix: string; data: UpdateNamespaceRequest }) =>
      client.registry.updateNamespace(prefix, data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.registry.all })
      options?.onSuccess?.(...args)
    },
    ...options,
  })
}

export function useArchiveNamespace(
  options?: Omit<UseMutationOptions<Namespace, Error, { prefix: string; archivedBy?: string }>, 'mutationFn'>,
) {
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ prefix, archivedBy }: { prefix: string; archivedBy?: string }) =>
      client.registry.archiveNamespace(prefix, archivedBy),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.registry.all })
      options?.onSuccess?.(...args)
    },
    ...options,
  })
}

export function useRestoreNamespace(
  options?: Omit<UseMutationOptions<Namespace, Error, { prefix: string; restoredBy?: string }>, 'mutationFn'>,
) {
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ prefix, restoredBy }: { prefix: string; restoredBy?: string }) =>
      client.registry.restoreNamespace(prefix, restoredBy),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.registry.all })
      options?.onSuccess?.(...args)
    },
    ...options,
  })
}

export function useDeleteNamespace(
  options?: Omit<UseMutationOptions<void, Error, { prefix: string; deletedBy?: string }>, 'mutationFn'>,
) {
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ prefix, deletedBy }: { prefix: string; deletedBy?: string }) =>
      client.registry.deleteNamespace(prefix, deletedBy),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.registry.all })
      options?.onSuccess?.(...args)
    },
    ...options,
  })
}

// ============================================================================
// REGISTRY ENTRY HOOKS (synonyms, merge, deactivate)
// ============================================================================

export function useAddSynonym(
  options?: Omit<UseMutationOptions<{ status: string; registry_id?: string; error?: string }, Error, AddSynonymRequest>, 'mutationFn'>,
) {
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (data: AddSynonymRequest) => client.registry.addSynonym(data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.registry.all })
      options?.onSuccess?.(...args)
    },
    ...options,
  })
}

export function useRemoveSynonym(
  options?: Omit<UseMutationOptions<{ status: string; registry_id?: string; error?: string }, Error, RemoveSynonymRequest>, 'mutationFn'>,
) {
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (data: RemoveSynonymRequest) => client.registry.removeSynonym(data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.registry.all })
      options?.onSuccess?.(...args)
    },
    ...options,
  })
}

export function useMergeEntries(
  options?: Omit<UseMutationOptions<{ status: string; preferred_id?: string; deprecated_id?: string; error?: string }, Error, MergeRequest>, 'mutationFn'>,
) {
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (data: MergeRequest) => client.registry.mergeEntries(data),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.registry.all })
      options?.onSuccess?.(...args)
    },
    ...options,
  })
}

export function useDeactivateEntry(
  options?: Omit<UseMutationOptions<{ status: string }, Error, { entryId: string; updatedBy?: string }>, 'mutationFn'>,
) {
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ entryId, updatedBy }: { entryId: string; updatedBy?: string }) =>
      client.registry.deactivateEntry(entryId, updatedBy),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.registry.all })
      options?.onSuccess?.(...args)
    },
    ...options,
  })
}
