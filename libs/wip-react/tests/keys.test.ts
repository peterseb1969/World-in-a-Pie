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
    const key = wipKeys.templates.detail('0190c000-0000-7000-0000-000000000001')
    expect(key).toContain('0190c000-0000-7000-0000-000000000001')
  })

  it('byValue keys are namespace-scoped and stay under templates.all', () => {
    const a = wipKeys.templates.byValue('CASE_RECORD', 'ns-a')
    const b = wipKeys.templates.byValue('CASE_RECORD', 'ns-b')
    const unscoped = wipKeys.templates.byValue('CASE_RECORD')
    expect(a).not.toEqual(b)
    expect(a).not.toEqual(unscoped)
    expect(unscoped).toEqual(wipKeys.templates.byValue('CASE_RECORD'))
    for (const key of [a, b, unscoped]) {
      expect(key.slice(0, wipKeys.templates.all.length)).toEqual([...wipKeys.templates.all])
    }
  })

  it('all keys start with wip prefix', () => {
    expect(wipKeys.terminologies.all[0]).toBe('wip')
    expect(wipKeys.documents.all[0]).toBe('wip')
    expect(wipKeys.registry.all[0]).toBe('wip')
  })
})
