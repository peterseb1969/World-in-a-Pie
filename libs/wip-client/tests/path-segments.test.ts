import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createWipClient } from '../src/client'
import type { WipClient } from '../src/client'

// A value interpolated into a request path is one path segment. Unencoded,
// `#` and what follows it never left the client, `?` turned the rest into a
// query string, and `/` added a segment: a template value like `FOO#2` was
// looked up as `FOO`.

describe('path segments are encoded', () => {
  let client: WipClient
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn().mockImplementation(async () =>
      new Response(JSON.stringify({}), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    )
    vi.stubGlobal('fetch', fetchMock)
    client = createWipClient({ baseUrl: 'http://localhost', auth: { type: 'api-key', key: 'test-key' } })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  function url(): URL {
    return new URL(String(fetchMock.mock.calls[0][0]))
  }

  it.each(['FOO#2', 'A?B', 'MY/APP', 'with space', '100%'])('a template value %s reaches the service whole', async (value) => {
    await client.templates.getTemplateByValue(value, { namespace: 'ns' })
    const u = url()
    expect(u.pathname).toBe(`/api/template-store/templates/by-value/${encodeURIComponent(value)}`)
    expect(u.hash).toBe('')
    expect(u.searchParams.get('namespace')).toBe('ns')
  })

  it('a version segment and a raw-lookup query stay apart', async () => {
    await client.templates.getTemplateByValueAndVersion('FOO#2', 3, { namespace: 'ns' })
    expect(url().pathname).toBe('/api/template-store/templates/by-value/FOO%232/versions/3')
  })

  it('an API key name is encoded', async () => {
    await client.registry.getAPIKey('key#1')
    expect(url().pathname).toBe('/api/registry/api-keys/key%231')
  })

  it('a terminology export names its terminology whole', async () => {
    await client.defStore.exportTerminology('COLOUR#2')
    const u = url()
    expect(u.pathname).toBe('/api/def-store/import-export/export/COLOUR%232')
    expect(u.searchParams.get('format')).toBe('json')
  })

  it('a backup archive download names its job whole', async () => {
    await client.documents.downloadBackupArchive('job?x')
    expect(url().pathname).toBe('/api/document-store/backup/jobs/job%3Fx/download')
  })
})

// Every value a service method puts into a request path goes through seg().
// A hand sweep missed two sites once; this reads the sources instead.
describe('no request path interpolates a value unencoded', () => {
  it('every ${...} in a path is seg(), the base path or a prebuilt query suffix', async () => {
    const { readdirSync, readFileSync } = await import('node:fs')
    const { join } = await import('node:path')
    const dir = join(__dirname, '../src/services')
    const allowed = /^((seg|encodeURIComponent)\(.*\)|this\.basePath|path|suffix|qs|query)$/
    const offenders: string[] = []
    for (const name of readdirSync(dir).filter((f) => f.endsWith('.ts'))) {
      readFileSync(join(dir, name), 'utf8').split('\n').forEach((line, i) => {
        for (const literal of line.match(/`[^`]*`/g) ?? []) {
          const body = literal.slice(1, -1)
          if (!/^(\$\{this\.basePath\})?\//.test(body)) continue // not a request path
          for (const m of body.matchAll(/\$\{([^}]*)\}/g)) {
            if (!allowed.test(m[1].trim())) offenders.push(`${name}:${i + 1} ${literal}`)
          }
        }
      })
    }
    expect(offenders).toEqual([])
  })
})

describe('uploadFile names the part after the given filename', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('a File keeps the filename it was given', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ file_id: 'f1' }), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const client = createWipClient({ baseUrl: 'http://localhost', auth: { type: 'api-key', key: 'k' } })
    const file = new File(['x'], 'original.txt', { type: 'text/plain' })
    await client.files.uploadFile(file, 'renamed.txt', undefined, 'ns')
    const form = fetchMock.mock.calls[0][1].body as FormData
    expect((form.get('file') as File).name).toBe('renamed.txt')
  })

  it('a File without a filename keeps its own name', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ file_id: 'f1' }), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const client = createWipClient({ baseUrl: 'http://localhost', auth: { type: 'api-key', key: 'k' } })
    await client.files.uploadFile(new File(['x'], 'original.txt'), undefined, undefined, 'ns')
    const form = fetchMock.mock.calls[0][1].body as FormData
    expect((form.get('file') as File).name).toBe('original.txt')
  })
})
