import { useState, useCallback } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { bulkImport, type BulkImportProgress, type BulkResponse } from '@wip/client'
import { wipKeys } from '../utils/keys.js'

interface UseBulkImportOptions<T> {
  writeFn: (batch: T[]) => Promise<BulkResponse>
  batchSize?: number
  continueOnError?: boolean
  invalidateKeys?: readonly unknown[]
}

export function useBulkImport<T>(options: UseBulkImportOptions<T>) {
  const [progress, setProgress] = useState<BulkImportProgress | null>(null)
  const queryClient = useQueryClient()

  const mutation = useMutation({
    mutationFn: (items: T[]) =>
      bulkImport(items, options.writeFn, {
        batchSize: options.batchSize,
        continueOnError: options.continueOnError,
        onProgress: setProgress,
      }),
    onSuccess: () => {
      const keys = options.invalidateKeys ?? wipKeys.all
      queryClient.invalidateQueries({ queryKey: keys as unknown[] })
    },
    onSettled: () => {
      // Keep progress visible for a moment, then clear
      setTimeout(() => setProgress(null), 2000)
    },
  })

  const reset = useCallback(() => {
    setProgress(null)
    mutation.reset()
  }, [mutation])

  return {
    ...mutation,
    progress,
    reset,
  }
}
