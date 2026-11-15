import type { BulkResponse } from '../types/common.js'

export interface BulkImportProgress {
  processed: number
  total: number
  succeeded: number
  failed: number
  /**
   * Batches whose request itself failed (an outage, a refused request): every
   * item of such a batch is counted in `failed`. Each entry names the batch by
   * its first item's offset in `items`, and carries the error.
   */
  rejected: BulkImportRejectedBatch[]
}

export interface BulkImportRejectedBatch {
  offset: number
  size: number
  error: unknown
}

export interface BulkImportOptions {
  batchSize?: number
  concurrency?: number
  /**
   * Go on after a batch with failed items, or a batch whose request failed
   * (default true). When false, the import stops after the first such batch;
   * the progress returned counts everything done until then.
   */
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
    rejected: [],
  }

  // Split items into batches, each with its offset in `items`
  const batches: { offset: number; items: T[] }[] = []
  for (let i = 0; i < items.length; i += batchSize) {
    batches.push({ offset: i, items: items.slice(i, i + batchSize) })
  }

  // A batch whose request rejects counts all its items as failed, so the
  // progress of the batches before it is never lost with the exception.
  const write = async (batch: { offset: number; items: T[] }): Promise<{ succeeded: number; failed: number }> => {
    try {
      return await writeFn(batch.items)
    } catch (error) {
      progress.rejected.push({ offset: batch.offset, size: batch.items.length, error })
      return { succeeded: 0, failed: batch.items.length }
    }
  }
  const snapshot = (): BulkImportProgress => ({ ...progress, rejected: [...progress.rejected] })

  if (concurrency <= 1) {
    // Sequential mode (default — safe for Pi)
    for (const batch of batches) {
      const result = await write(batch)

      progress.processed += batch.items.length
      progress.succeeded += result.succeeded
      progress.failed += result.failed

      options?.onProgress?.(snapshot())

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
          const result = await write(batch)
          return { batch, result }
        }),
      )

      for (const { batch, result } of results) {
        progress.processed += batch.items.length
        progress.succeeded += result.succeeded
        progress.failed += result.failed

        if (!continueOnError && result.failed > 0) {
          stopped = true
        }
      }

      options?.onProgress?.(snapshot())
    }
  }

  return snapshot()
}
