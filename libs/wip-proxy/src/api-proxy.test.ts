import { describe, it, expect } from 'vitest'
import { applyDefaultNamespace } from './api-proxy.js'

const QUERY = '/api/document-store/documents/query'

describe('applyDefaultNamespace (CASE-457 Fix #3)', () => {
  it('is a no-op when no defaultNamespace is configured', () => {
    expect(applyDefaultNamespace(QUERY, undefined)).toBe(QUERY)
    expect(applyDefaultNamespace(QUERY, '')).toBe(QUERY)
  })

  it('appends ?namespace= on the bare query endpoint', () => {
    expect(applyDefaultNamespace(QUERY, 'dev-wip-song')).toBe(
      `${QUERY}?namespace=dev-wip-song`,
    )
  })

  it('appends &namespace= when the query endpoint already has a query string', () => {
    expect(applyDefaultNamespace(`${QUERY}?page=1`, 'dev-wip-song')).toBe(
      `${QUERY}?page=1&namespace=dev-wip-song`,
    )
  })

  it('does not double-scope when the caller already passed namespace', () => {
    const scoped = `${QUERY}?namespace=other`
    expect(applyDefaultNamespace(scoped, 'dev-wip-song')).toBe(scoped)
    const scopedMid = `${QUERY}?page=1&namespace=other`
    expect(applyDefaultNamespace(scopedMid, 'dev-wip-song')).toBe(scopedMid)
  })

  it('URL-encodes the namespace value', () => {
    expect(applyDefaultNamespace(QUERY, 'a b/c')).toBe(`${QUERY}?namespace=a%20b%2Fc`)
  })

  it('leaves non-query endpoints untouched (files, writes, other services)', () => {
    // Files take namespace as a form field; document writes take it in the
    // body; injecting the query param there 422s. Only the query endpoint.
    for (const path of [
      '/api/document-store/documents', // POST write / GET list
      '/api/document-store/files',
      '/api/document-store/documents/query-something', // not the exact path
      '/api/registry/namespaces',
      '/api/template-store/templates',
    ]) {
      expect(applyDefaultNamespace(path, 'dev-wip-song')).toBe(path)
      expect(applyDefaultNamespace(`${path}?page=1`, 'dev-wip-song')).toBe(`${path}?page=1`)
    }
  })
})
