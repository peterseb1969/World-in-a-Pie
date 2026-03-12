import type { BulkResponse } from '../types/common.js'

export interface BulkImportProgress {
  processed: number
  total: number
  succeeded: number
  failed: number
}

export interface BulkImportOptions {
  batchSize?: number
  continueOnError?: boolean
  onProgress?: (progress: BulkImportProgress) => void
}

/**
 * Import items in sequential batches, calling writeFn for each chunk.
 * Sequential to be Pi-friendly (avoids overwhelming the API).
 */
export async function bulkImport<T>(
  items: T[],
  writeFn: (batch: T[]) => Promise<BulkResponse>,
  options?: BulkImportOptions,
): Promise<BulkImportProgress> {
  const batchSize = options?.batchSize ?? 100
  const continueOnError = options?.continueOnError ?? true

  const progress: BulkImportProgress = {
    processed: 0,
    total: items.length,
    succeeded: 0,
    failed: 0,
  }

  for (let i = 0; i < items.length; i += batchSize) {
    const batch = items.slice(i, i + batchSize)
    const result = await writeFn(batch)

    progress.processed += batch.length
    progress.succeeded += result.succeeded
    progress.failed += result.failed

    options?.onProgress?.({ ...progress })

    if (!continueOnError && result.failed > 0) {
      break
    }
  }

  return progress
}
