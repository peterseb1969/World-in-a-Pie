import type { BulkResponse } from '../types/common.js'

export interface BulkImportProgress {
  processed: number
  total: number
  succeeded: number
  failed: number
}

export interface BulkImportOptions {
  batchSize?: number
  concurrency?: number
  continueOnError?: boolean
  onProgress?: (progress: BulkImportProgress) => void
}

/**
 * Import items in batches, calling writeFn for each chunk.
 *
 * Supports concurrent batches via `concurrency` option (default: 1 = sequential).
 * Sequential mode is safest for Pi deployments; concurrency ≥ 2 improves throughput
 * on faster hardware by overlapping network I/O with server processing.
 */
export async function bulkImport<T>(
  items: T[],
  writeFn: (batch: T[]) => Promise<BulkResponse>,
  options?: BulkImportOptions,
): Promise<BulkImportProgress> {
  const batchSize = options?.batchSize ?? 100
  const concurrency = options?.concurrency ?? 1
  const continueOnError = options?.continueOnError ?? true

  const progress: BulkImportProgress = {
    processed: 0,
    total: items.length,
    succeeded: 0,
    failed: 0,
  }

  // Split items into batches
  const batches: T[][] = []
  for (let i = 0; i < items.length; i += batchSize) {
    batches.push(items.slice(i, i + batchSize))
  }

  if (concurrency <= 1) {
    // Sequential mode (default — safe for Pi)
    for (const batch of batches) {
      const result = await writeFn(batch)

      progress.processed += batch.length
      progress.succeeded += result.succeeded
      progress.failed += result.failed

      options?.onProgress?.({ ...progress })

      if (!continueOnError && result.failed > 0) {
        break
      }
    }
  } else {
    // Concurrent mode — process up to `concurrency` batches in parallel
    let stopped = false
    let batchIndex = 0

    while (batchIndex < batches.length && !stopped) {
      const chunk = batches.slice(batchIndex, batchIndex + concurrency)
      batchIndex += chunk.length

      const results = await Promise.all(
        chunk.map(async (batch) => {
          const result = await writeFn(batch)
          return { batch, result }
        }),
      )

      for (const { batch, result } of results) {
        progress.processed += batch.length
        progress.succeeded += result.succeeded
        progress.failed += result.failed

        if (!continueOnError && result.failed > 0) {
          stopped = true
        }
      }

      options?.onProgress?.({ ...progress })
    }
  }

  return progress
}
