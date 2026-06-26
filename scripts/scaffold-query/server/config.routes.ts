/**
 * Runtime configuration endpoints (CASE-509, generalized from CASE-508).
 *
 * Admin-only — gated by requireAdmin() at the mount in index.ts. Lets an
 * operator set/rotate the Anthropic API key in a running app without a
 * redeploy. The key is a SECRET: it is never stored in a WIP document (that
 * would sync to the Postgres reporting layer and land in backups) and is never
 * echoed back to the caller — responses carry only configured/source/last4.
 */
import { Router } from 'express'
import { getKeyStatus, setAnthropicKey, validateKey } from './agent.js'

const router = Router()

// Masked status — is a key configured, where from, last-4.
router.get('/anthropic-key', (_req, res) => {
  res.json(getKeyStatus())
})

// Set/rotate the key. Validates against the API before accepting; persists to
// ANTHROPIC_API_KEY_FILE (0600) by default so it survives a restart.
router.post('/anthropic-key', async (req, res) => {
  const key = typeof req.body?.key === 'string' ? req.body.key.trim() : ''
  const persist = req.body?.persist !== false  // default true
  if (!key) {
    res.status(400).json({ error: 'key is required' })
    return
  }
  const v = await validateKey(key)
  if (!v.ok) {
    res.status(400).json({ error: `Key rejected: ${v.error}` })
    return
  }
  try {
    const status = await setAnthropicKey(key, { persist })
    res.json(status)
  } catch (err: any) {
    res.status(500).json({ error: err?.message || 'Failed to set key' })
  }
})

export default router
