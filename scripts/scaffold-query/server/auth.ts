/**
 * OIDC authentication middleware for WIP apps.
 *
 * When OIDC_ISSUER is set, redirects unauthenticated users to Dex login.
 * After login, sets X-WIP-User and X-WIP-Groups on the request so
 * @wip/proxy can forward them to WIP services.
 *
 * When OIDC_ISSUER is not set, auth is disabled (local dev mode).
 */
import type { Request, Response, NextFunction, RequestHandler } from 'express'
import * as client from 'openid-client'

// Session augmentation for TypeScript
declare module 'express-session' {
  interface SessionData {
    user?: { email: string; groups: string[]; name?: string }
    returnTo?: string
  }
}

let oidcConfig: client.Configuration | null = null

const OIDC_ISSUER = process.env.OIDC_ISSUER
const OIDC_CLIENT_ID = process.env.OIDC_CLIENT_ID || 'wip-apps'
const OIDC_CLIENT_SECRET = process.env.OIDC_CLIENT_SECRET || 'wip-apps-secret'

/** Public paths that skip authentication */
const PUBLIC_PATHS = ['/api/health', '/auth/callback', '/auth/logout']

/**
 * Initialize OIDC client. Call once at startup.
 * Returns true if auth is enabled, false if OIDC_ISSUER is not set.
 */
export async function initAuth(): Promise<boolean> {
  if (!OIDC_ISSUER) {
    console.log('[auth] OIDC_ISSUER not set — auth disabled (local dev mode)')
    return false
  }

  const issuer = new URL(OIDC_ISSUER)
  oidcConfig = await client.discovery(issuer, OIDC_CLIENT_ID, OIDC_CLIENT_SECRET)
  console.log(`[auth] OIDC configured: issuer=${OIDC_ISSUER}, client=${OIDC_CLIENT_ID}`)
  return true
}

/**
 * Get the callback URL for OIDC redirects.
 */
function getCallbackUrl(req: Request): string {
  const proto = req.headers['x-forwarded-proto'] || req.protocol
  const host = req.headers['x-forwarded-host'] || req.get('host')
  return `${proto}://${host}/auth/callback`
}

/**
 * Express middleware that requires OIDC authentication.
 * Skips auth for PUBLIC_PATHS and when OIDC_ISSUER is not set.
 */
export function requireAuth(): RequestHandler {
  return (req: Request, res: Response, next: NextFunction): void => {
    // Auth disabled — pass through
    if (!oidcConfig) {
      next()
      return
    }

    // Public paths skip auth
    if (PUBLIC_PATHS.some(p => req.path.startsWith(p))) {
      next()
      return
    }

    // Already authenticated — inject identity headers
    if (req.session.user) {
      req.headers['x-wip-user'] = req.session.user.email
      req.headers['x-wip-groups'] = req.session.user.groups.join(',')
      req.headers['x-wip-auth-method'] = 'gateway_oidc'
      next()
      return
    }

    // Not authenticated — redirect to Dex
    const callbackUrl = getCallbackUrl(req)
    const codeVerifier = client.randomPKCECodeVerifier()
    const codeChallenge = client.calculatePKCECodeChallenge(codeVerifier)

    // Store PKCE verifier and return URL in session
    req.session.returnTo = req.originalUrl
    ;(req.session as any).codeVerifier = codeVerifier

    codeChallenge.then(challenge => {
      const params = new URLSearchParams({
        client_id: OIDC_CLIENT_ID,
        response_type: 'code',
        redirect_uri: callbackUrl,
        scope: 'openid email profile groups',
        code_challenge: challenge,
        code_challenge_method: 'S256',
      })

      const authUrl = `${oidcConfig!.serverMetadata().authorization_endpoint}?${params}`
      res.redirect(authUrl)
    }).catch(next)
  }
}

/**
 * Handle OIDC callback — exchange code for tokens, create session.
 */
export async function handleCallback(req: Request, res: Response): Promise<void> {
  if (!oidcConfig) {
    res.status(500).json({ error: 'Auth not configured' })
    return
  }

  try {
    const callbackUrl = getCallbackUrl(req)
    const codeVerifier = (req.session as any).codeVerifier

    const callbackParams = new URLSearchParams(req.url.split('?')[1] || '')
    const tokens = await client.authorizationCodeGrant(
      oidcConfig,
      new URL(`${callbackUrl}?${callbackParams}`),
      { pkceCodeVerifier: codeVerifier },
    )

    const claims = tokens.claims()!
    const groups = (claims as any).groups || []

    req.session.user = {
      email: claims.email as string || claims.sub,
      groups: Array.isArray(groups) ? groups : [groups],
      name: claims.name as string | undefined,
    }

    // Clean up PKCE verifier
    delete (req.session as any).codeVerifier

    const returnTo = req.session.returnTo || '/'
    delete req.session.returnTo
    res.redirect(returnTo)
  } catch (err) {
    console.error('[auth] Callback error:', err)
    res.status(401).json({ error: 'Authentication failed' })
  }
}

/**
 * Handle logout — destroy session and optionally redirect to Dex end-session.
 */
export function handleLogout(req: Request, res: Response): void {
  req.session.destroy(() => {
    res.redirect('/')
  })
}
