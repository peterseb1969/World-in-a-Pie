import { useMutation, useQueryClient } from '@tanstack/react-query'
import type { UseMutationOptions } from '@tanstack/react-query'
import type {
  BulkResultItem,
  BulkResponse,
  CreateTerminologyRequest,
  CreateTermRequest,
  CreateTemplateRequest,
  CreateDocumentRequest,
  FileUploadMetadata,
  FileEntity,
} from '@wip/client'
import { useWipClient } from '../provider.js'
import { wipKeys } from '../utils/keys.js'

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

export function useDeleteDocument(
  options?: Omit<UseMutationOptions<BulkResultItem, Error, { id: string; updatedBy?: string }>, 'mutationFn'>,
) {
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, updatedBy }) => client.documents.deleteDocument(id, updatedBy),
    onSuccess: (...args) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.documents.all })
      options?.onSuccess?.(...args)
    },
    ...options,
  })
}
