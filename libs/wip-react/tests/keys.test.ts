import { describe, it, expect } from 'vitest'
import { wipKeys } from '../src/utils/keys'

describe('wipKeys', () => {
  it('generates stable terminology list keys', () => {
    const key1 = wipKeys.terminologies.list({ page: 1 })
    const key2 = wipKeys.terminologies.list({ page: 1 })
    expect(key1).toEqual(key2)
  })

  it('generates unique keys for different params', () => {
    const key1 = wipKeys.documents.list({ page: 1 })
    const key2 = wipKeys.documents.list({ page: 2 })
    expect(key1).not.toEqual(key2)
  })

  it('detail keys include entity id', () => {
    const key = wipKeys.templates.detail('TPL-001')
    expect(key).toContain('TPL-001')
  })

  it('all keys start with wip prefix', () => {
    expect(wipKeys.terminologies.all[0]).toBe('wip')
    expect(wipKeys.documents.all[0]).toBe('wip')
    expect(wipKeys.registry.all[0]).toBe('wip')
  })
})
