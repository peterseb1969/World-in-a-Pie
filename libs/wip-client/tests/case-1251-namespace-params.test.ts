import { describe, it, expect, vi, afterEach } from 'vitest'
import { createWipClient } from '../src/client'

// CASE-1251 (review of aa040249, R1): these methods' routes take a
// `namespace` that scopes a value-form or synonym id, but the client sent
// none, so a multi-namespace key could not address such an id through them.

const bulkOk = { results: [{ index: 0, status: 'updated', id: 'x' }], total: 1, succeeded: 1, failed: 0 }

function client() {
  return createWipClient({ baseUrl: 'http://localhost', auth: { type: 'api-key', key: 'k' } })
}

function capture(body: unknown = {}) {
  const urls: string[] = []
  vi.stubGlobal('fetch', vi.fn().mockImplementation(async (url: string) => {
    urls.push(String(url))
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }))
  return urls
}

afterEach(() => {
  vi.restoreAllMocks()
})

const cases: Array<[string, (c: ReturnType<typeof client>) => Promise<unknown>, unknown?]> = [
  ['getVersions', (c) => c.documents.getVersions('D', 'ns1')],
  ['getVersion', (c) => c.documents.getVersion('D', 2, 'ns1')],
  ['getLatestDocument', (c) => c.documents.getLatestDocument('D', 'ns1')],
  ['getTemplate', (c) => c.templates.getTemplate('T', 2, 'ns1')],
  ['getTemplateRaw', (c) => c.templates.getTemplateRaw('T', undefined, 'ns1')],
  ['updateTemplate', (c) => c.templates.updateTemplate('T', {}, 'ns1'), bulkOk],
  ['deleteTemplate', (c) => c.templates.deleteTemplate('T', { namespace: 'ns1' }), bulkOk],
  ['validateTemplate', (c) => c.templates.validateTemplate('T', {}, 'ns1')],
  ['cascadeTemplate', (c) => c.templates.cascadeTemplate('T', 'ns1')],
  ['updateTerminology', (c) => c.defStore.updateTerminology('L', {}, 'ns1'), bulkOk],
  ['deleteTerminology', (c) => c.defStore.deleteTerminology('L', { namespace: 'ns1' }), bulkOk],
  ['getTerminologyAuditLog', (c) => c.defStore.getTerminologyAuditLog('L', { namespace: 'ns1' })],
]

describe('a namespace given to a by-id method reaches its route', () => {
  it.each(cases)('%s sends namespace', async (_name, call, body) => {
    const urls = capture(body ?? {})
    await call(client())
    expect(urls).toHaveLength(1)
    expect(new URL(urls[0]).searchParams.get('namespace')).toBe('ns1')
  })

  it.each(cases)('%s sends none when not given', async (name, _call, body) => {
    const urls = capture(body ?? {})
    const c = client()
    const without: Record<string, () => Promise<unknown>> = {
      getVersions: () => c.documents.getVersions('D'),
      getVersion: () => c.documents.getVersion('D', 2),
      getLatestDocument: () => c.documents.getLatestDocument('D'),
      getTemplate: () => c.templates.getTemplate('T'),
      getTemplateRaw: () => c.templates.getTemplateRaw('T'),
      updateTemplate: () => c.templates.updateTemplate('T', {}),
      deleteTemplate: () => c.templates.deleteTemplate('T'),
      validateTemplate: () => c.templates.validateTemplate('T'),
      cascadeTemplate: () => c.templates.cascadeTemplate('T'),
      updateTerminology: () => c.defStore.updateTerminology('L', {}),
      deleteTerminology: () => c.defStore.deleteTerminology('L'),
      getTerminologyAuditLog: () => c.defStore.getTerminologyAuditLog('L'),
    }
    await without[name]()
    expect(new URL(urls[0]).searchParams.has('namespace')).toBe(false)
  })

  it('getTemplate keeps its version', async () => {
    const urls = capture()
    await client().templates.getTemplate('T', 3, 'ns1')
    expect(new URL(urls[0]).searchParams.get('version')).toBe('3')
  })
})
