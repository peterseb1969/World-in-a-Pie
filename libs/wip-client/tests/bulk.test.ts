import { describe, it, expect, vi } from 'vitest'
import { bulkImport } from '../src/utils/bulk-import'
import type { BulkResponse } from '../src/types/common'

describe('bulkImport', () => {
  const makeBulkResponse = (count: number, failed = 0): BulkResponse => ({
    results: Array.from({ length: count }, (_, i) => ({
      index: i,
      status: i < count - failed ? 'created' : 'error',
      error: i < count - failed ? undefined : 'failed',
    })),
    total: count,
    succeeded: count - failed,
    failed,
  })

  it('processes all items in batches', async () => {
    const items = Array.from({ length: 25 }, (_, i) => ({ value: `item-${i}` }))
    const writeFn = vi.fn().mockImplementation((batch) => Promise.resolve(makeBulkResponse(batch.length)))

    const result = await bulkImport(items, writeFn, { batchSize: 10 })

    expect(writeFn).toHaveBeenCalledTimes(3) // 10 + 10 + 5
    expect(result.processed).toBe(25)
    expect(result.succeeded).toBe(25)
    expect(result.failed).toBe(0)
    expect(result.total).toBe(25)
  })

  it('reports progress', async () => {
    const items = Array.from({ length: 20 }, (_, i) => ({ value: `item-${i}` }))
    const writeFn = vi.fn().mockImplementation((batch) => Promise.resolve(makeBulkResponse(batch.length)))
    const onProgress = vi.fn()

    await bulkImport(items, writeFn, { batchSize: 10, onProgress })

    expect(onProgress).toHaveBeenCalledTimes(2)
    expect(onProgress.mock.calls[0][0]).toEqual({
      processed: 10,
      total: 20,
      succeeded: 10,
      failed: 0,
    })
    expect(onProgress.mock.calls[1][0]).toEqual({
      processed: 20,
      total: 20,
      succeeded: 20,
      failed: 0,
    })
  })

  it('stops on error when continueOnError is false', async () => {
    const items = Array.from({ length: 30 }, (_, i) => ({ value: `item-${i}` }))
    const writeFn = vi.fn()
      .mockResolvedValueOnce(makeBulkResponse(10, 2))
      .mockResolvedValueOnce(makeBulkResponse(10))

    const result = await bulkImport(items, writeFn, {
      batchSize: 10,
      continueOnError: false,
    })

    expect(writeFn).toHaveBeenCalledTimes(1)
    expect(result.processed).toBe(10)
    expect(result.failed).toBe(2)
  })

  it('continues on error by default', async () => {
    const items = Array.from({ length: 20 }, (_, i) => ({ value: `item-${i}` }))
    const writeFn = vi.fn()
      .mockResolvedValueOnce(makeBulkResponse(10, 3))
      .mockResolvedValueOnce(makeBulkResponse(10))

    const result = await bulkImport(items, writeFn, { batchSize: 10 })

    expect(writeFn).toHaveBeenCalledTimes(2)
    expect(result.processed).toBe(20)
    expect(result.succeeded).toBe(17)
    expect(result.failed).toBe(3)
  })

  it('handles empty items array', async () => {
    const writeFn = vi.fn()

    const result = await bulkImport([], writeFn)

    expect(writeFn).not.toHaveBeenCalled()
    expect(result.processed).toBe(0)
    expect(result.total).toBe(0)
  })

  it('processes batches concurrently when concurrency > 1', async () => {
    const items = Array.from({ length: 30 }, (_, i) => ({ value: `item-${i}` }))
    let concurrent = 0
    let maxConcurrent = 0

    const writeFn = vi.fn().mockImplementation(async (batch) => {
      concurrent++
      maxConcurrent = Math.max(maxConcurrent, concurrent)
      await new Promise((r) => setTimeout(r, 10))
      concurrent--
      return makeBulkResponse(batch.length)
    })

    const result = await bulkImport(items, writeFn, {
      batchSize: 10,
      concurrency: 3,
    })

    expect(writeFn).toHaveBeenCalledTimes(3)
    expect(maxConcurrent).toBe(3) // All 3 batches ran in parallel
    expect(result.processed).toBe(30)
    expect(result.succeeded).toBe(30)
  })
})
