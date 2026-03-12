import { useQuery } from '@tanstack/react-query'
import type { UseQueryOptions } from '@tanstack/react-query'
import type { FileListResponse, FileEntity, FileDownloadResponse, FileQueryParams } from '@wip/client'
import { useWipClient } from '../provider.js'
import { wipKeys } from '../utils/keys.js'
import { STALE_TIMES } from '../utils/defaults.js'

export function useFiles(
  params?: FileQueryParams,
  options?: Omit<UseQueryOptions<FileListResponse>, 'queryKey' | 'queryFn'>,
) {
  const client = useWipClient()
  return useQuery({
    queryKey: wipKeys.files.list(params),
    queryFn: () => client.files.listFiles(params),
    staleTime: STALE_TIMES.files,
    ...options,
  })
}

export function useFile(
  id: string,
  options?: Omit<UseQueryOptions<FileEntity>, 'queryKey' | 'queryFn'>,
) {
  const client = useWipClient()
  return useQuery({
    queryKey: wipKeys.files.detail(id),
    queryFn: () => client.files.getFile(id),
    staleTime: STALE_TIMES.files,
    enabled: !!id,
    ...options,
  })
}

export function useDownloadUrl(
  id: string,
  options?: Omit<UseQueryOptions<FileDownloadResponse>, 'queryKey' | 'queryFn'>,
) {
  const client = useWipClient()
  return useQuery({
    queryKey: wipKeys.files.downloadUrl(id),
    queryFn: () => client.files.getDownloadUrl(id),
    staleTime: 60 * 1000, // URLs expire — shorter stale time
    enabled: !!id,
    ...options,
  })
}
