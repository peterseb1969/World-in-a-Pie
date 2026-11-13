import { useState, useCallback, useEffect, useRef } from 'react'
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
  // The timer that clears the finished run's progress: cancelled when a new
  // run starts (it would wipe that run's progress) and on unmount.
  const clearTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const cancelClear = useCallback(() => {
    if (clearTimer.current !== null) {
      clearTimeout(clearTimer.current)
      clearTimer.current = null
    }
  }, [])
  useEffect(() => cancelClear, [cancelClear])

  const mutation = useMutation({
    onMutate: () => {
      cancelClear()
    },
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
      cancelClear()
      clearTimer.current = setTimeout(() => {
        clearTimer.current = null
        setProgress(null)
      }, 2000)
    },
  })

  const { reset: resetMutation } = mutation
  const reset = useCallback(() => {
    cancelClear()
    setProgress(null)
    resetMutation()
  }, [cancelClear, resetMutation])

  return {
    ...mutation,
    progress,
    reset,
  }
}
