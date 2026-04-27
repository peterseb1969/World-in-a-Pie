/**
 * Bootstrap routes — wires the bootstrap library to two HTTP endpoints
 * the React BootstrapGate consumes.
 *
 *   GET  /server-api/bootstrap/status  → { status: BootstrapStatus }
 *   POST /server-api/bootstrap/run     → SSE stream of BootstrapProgress
 *
 * Mount this on your Express app under whatever path the React side
 * expects. APP-CT mounts at `/server-api`; if your scaffold uses a
 * different prefix, only the React side's `serverApiUrl()` helper
 * needs to know.
 *
 * The SSE stream is what makes the bootstrap UX bearable — a five-second
 * silent spinner is much worse than five seconds of "Creating 12
 * terminologies... Creating 47 terms... Waiting for cache... Done."
 *
 * REQUIRED before this works:
 *   - server/lib/sse.ts with initSSE / sendSSE / endSSE helpers (APP-CT
 *     ships these in ~30 lines; see the bootstrap.server.ts.template
 *     header for the contract).
 *   - server/lib/bootstrap.ts (this directory) imported as `./lib/bootstrap.js`.
 *   - Your Express app already has the `/server-api` mount and JSON
 *     middleware.
 */

import { Router } from 'express'
import { checkStatus, runBootstrap, type BootstrapProgress } from '../lib/bootstrap.js'
import { initSSE, sendSSE, endSSE } from '../lib/sse.js'

const router = Router()

/**
 * GET /server-api/bootstrap/status
 * Check if WIP is reachable and the namespace exists.
 */
router.get('/bootstrap/status', async (_req, res) => {
  try {
    const status = await checkStatus()
    res.json({ status })
  } catch (err) {
    res.json({ status: 'wip_unreachable', error: (err as Error).message })
  }
})

/**
 * POST /server-api/bootstrap/run
 * Execute the bootstrap. Streams progress via SSE; client reads with a
 * fetch-stream reader (no eventsource library needed). On completion,
 * emits one `complete` event and closes the stream; on failure, emits
 * one `error` event then closes.
 */
router.post('/bootstrap/run', (req, res) => {
  initSSE(res)

  let clientConnected = true
  req.on('close', () => { clientConnected = false })

  runBootstrap((p: BootstrapProgress) => {
    if (clientConnected) sendSSE(res, p.done ? 'complete' : 'progress', p)
  })
    .then(() => {
      if (clientConnected) endSSE(res)
    })
    .catch((err) => {
      if (clientConnected) {
        sendSSE(res, 'error', { message: (err as Error).message })
        endSSE(res)
      }
    })
})

export default router
