#!/usr/bin/env tsx
/**
 * Smoke test for @wip/client against a running WIP instance.
 *
 * Usage:
 *   npx tsx scripts/smoke-test.ts [--base-url https://localhost:8443] [--api-key dev_master_key_for_testing]
 *
 * For local dev with self-signed certs, NODE_TLS_REJECT_UNAUTHORIZED=0 is set automatically.
 */

// Allow self-signed certs for local dev
process.env.NODE_TLS_REJECT_UNAUTHORIZED = '0'

import {
  createWipClient,
  WipNotFoundError,
  WipBulkItemError,
  WipValidationError,
  WipAuthError,
} from '../src/index.js'

const args = process.argv.slice(2)
function getArg(name: string, fallback: string): string {
  const idx = args.indexOf(`--${name}`)
  return idx !== -1 && args[idx + 1] ? args[idx + 1] : fallback
}

const baseUrl = process.env.WIP_TEST_BASE_URL ?? getArg('base-url', 'https://localhost:8443')
const apiKey = process.env.WIP_TEST_API_KEY ?? getArg('api-key', 'dev_master_key_for_testing')

let passed = 0
let failed = 0

async function test(name: string, fn: () => Promise<void>) {
  try {
    await fn()
    console.log(`  PASS  ${name}`)
    passed++
  } catch (err) {
    console.log(`  FAIL  ${name}`)
    console.log(`        ${err instanceof Error ? err.message : err}`)
    failed++
  }
}

function assert(condition: boolean, message: string) {
  if (!condition) throw new Error(`Assertion failed: ${message}`)
}

async function main() {
  console.log(`\nSmoke test: ${baseUrl} (API key: ${apiKey.slice(0, 8)}...)\n`)

  const client = createWipClient({
    baseUrl,
    auth: { type: 'api-key', key: apiKey },
    timeout: 10_000,
    retry: { maxRetries: 1, baseDelayMs: 500 },
  })

  // --- Registry ---

  await test('List namespaces', async () => {
    const namespaces = await client.registry.listNamespaces()
    assert(Array.isArray(namespaces), 'Expected array')
    assert(namespaces.length > 0, 'Expected at least one namespace')
    console.log(`        Found ${namespaces.length} namespace(s): ${namespaces.map(n => n.prefix).join(', ')}`)
  })

  // --- Terminologies ---

  await test('List terminologies (paginated)', async () => {
    const result = await client.defStore.listTerminologies({ page: 1, page_size: 5 })
    assert(typeof result.total === 'number', 'Expected total field')
    assert(Array.isArray(result.items), 'Expected items array')
    assert(typeof result.pages === 'number', 'Expected pages field')
    console.log(`        ${result.total} terminology(ies), ${result.pages} page(s)`)
  })

  // --- Create + read-back terminology ---

  const testValue = `SMOKE_TEST_${Date.now()}`

  await test('Create terminology (single-item)', async () => {
    const result = await client.defStore.createTerminology({
      value: testValue,
      label: 'Smoke Test Terminology',
      description: 'Created by smoke-test.ts — safe to delete',
    })
    assert(result.status === 'created', `Expected status "created", got "${result.status}"`)
    assert(typeof result.id === 'string', 'Expected id')
    console.log(`        Created: ${result.id}`)
  })

  let terminologyId: string | undefined

  await test('Get terminology by list + value filter', async () => {
    const result = await client.defStore.listTerminologies({ value: testValue })
    assert(result.items.length === 1, `Expected 1 result, got ${result.items.length}`)
    assert(result.items[0].value === testValue, 'Value mismatch')
    terminologyId = result.items[0].terminology_id
    console.log(`        Found: ${terminologyId}`)
  })

  // --- Create term ---

  if (terminologyId) {
    await test('Create term', async () => {
      const result = await client.defStore.createTerm(terminologyId!, {
        value: 'TEST_TERM_A',
        label: 'Test Term A',
      })
      assert(result.status === 'created', `Expected "created", got "${result.status}"`)
    })

    await test('List terms for terminology', async () => {
      const result = await client.defStore.listTerms(terminologyId!)
      assert(result.items.length >= 1, 'Expected at least 1 term')
      assert(result.terminology_id === terminologyId, 'Terminology ID mismatch')
    })

    // --- Duplicate should return error via BulkItemError ---

    await test('Duplicate term throws WipBulkItemError', async () => {
      try {
        await client.defStore.createTerm(terminologyId!, {
          value: 'TEST_TERM_A',
          label: 'Test Term A',
        })
        throw new Error('Should have thrown')
      } catch (err) {
        assert(err instanceof WipBulkItemError, `Expected WipBulkItemError, got ${(err as Error).constructor.name}`)
      }
    })
  }

  // --- Templates ---

  await test('List templates', async () => {
    const result = await client.templates.listTemplates({ page: 1, page_size: 5, latest_only: true })
    assert(typeof result.total === 'number', 'Expected total')
    console.log(`        ${result.total} template(s)`)
  })

  // --- Documents ---

  await test('List documents', async () => {
    const result = await client.documents.listDocuments({ page: 1, page_size: 5 })
    assert(typeof result.total === 'number', 'Expected total')
    console.log(`        ${result.total} document(s)`)
  })

  // --- 404 error handling ---

  await test('Get nonexistent terminology throws WipNotFoundError', async () => {
    try {
      await client.defStore.getTerminology('nonexistent-id-that-does-not-exist')
      throw new Error('Should have thrown')
    } catch (err) {
      assert(err instanceof WipNotFoundError, `Expected WipNotFoundError, got ${(err as Error).constructor.name}`)
    }
  })

  // --- Auth error ---

  await test('Bad API key throws WipAuthError', async () => {
    const badClient = createWipClient({
      baseUrl,
      auth: { type: 'api-key', key: 'invalid_key' },
      retry: { maxRetries: 0 },
    })
    try {
      await badClient.defStore.listTerminologies()
      // Some WIP setups allow unauthenticated reads — skip if it succeeds
      console.log('        (Skipped — unauthenticated reads allowed)')
    } catch (err) {
      assert(err instanceof WipAuthError, `Expected WipAuthError, got ${(err as Error).constructor.name}`)
    }
  })

  // --- Reporting ---

  await test('Reporting health check', async () => {
    const healthy = await client.reporting.healthCheck()
    assert(typeof healthy === 'boolean', 'Expected boolean')
    console.log(`        Healthy: ${healthy}`)
  })

  // --- Cleanup ---

  if (terminologyId) {
    await test('Delete test terminology', async () => {
      const result = await client.defStore.deleteTerminology(terminologyId!)
      assert(result.status === 'deleted' || result.status === 'deactivated', `Unexpected status: ${result.status}`)
    })
  }

  // --- Summary ---

  console.log(`\n  ${passed + failed} tests: ${passed} passed, ${failed} failed\n`)
  process.exit(failed > 0 ? 1 : 0)
}

main().catch((err) => {
  console.error('Fatal error:', err)
  process.exit(1)
})
