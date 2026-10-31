import { test } from 'node:test'
import assert from 'node:assert/strict'
import { backoffMs, errorStatus, retryDecision } from './agent-retry.ts'

test('a platform that is still starting is retried', () => {
  // The MCP server's answer while it cannot reach the Registry.
  assert.equal(retryDecision(Object.assign(new Error('Registry unreachable'), { code: 503 })), 'retry')
  assert.equal(retryDecision(Object.assign(new Error('connect ECONNREFUSED'), { code: 'ECONNREFUSED' })), 'retry')
  assert.equal(retryDecision(new Error('timed out')), 'retry')
  assert.equal(retryDecision(undefined), 'retry')
})

test('a rejected key stops the retries', () => {
  assert.equal(retryDecision(Object.assign(new Error('Invalid API key'), { code: 401 })), 'stop')
  assert.equal(retryDecision({ status: 403 }), 'stop')
})

test('the status is read from code or status, numbers only', () => {
  assert.equal(errorStatus({ code: 503 }), 503)
  assert.equal(errorStatus({ status: 401 }), 401)
  assert.equal(errorStatus({ code: 'ECONNREFUSED' }), null)
  assert.equal(errorStatus('boom'), null)
})

test('delays grow and are capped', () => {
  assert.deepEqual([0, 1, 2, 3, 4, 5, 6, 10].map((n) => backoffMs(n)),
    [1000, 2000, 4000, 8000, 16000, 30000, 30000, 30000])
})
