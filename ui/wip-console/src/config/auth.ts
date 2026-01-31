/**
 * Authentication configuration for WIP Console
 *
 * Supports two authentication modes:
 * - OIDC: OAuth2/OpenID Connect via Dex (or any OIDC provider)
 * - API Key: Legacy X-API-Key header authentication
 *
 * Environment variables:
 * - VITE_OIDC_AUTHORITY: OIDC provider URL (default: http://localhost:5556/dex)
 * - VITE_OIDC_CLIENT_ID: OAuth2 client ID (default: wip-console)
 * - VITE_OIDC_CLIENT_SECRET: OAuth2 client secret (optional, for confidential clients)
 */

import type { UserManagerSettings } from 'oidc-client-ts'

// Get OIDC authority from environment
const authority = import.meta.env.VITE_OIDC_AUTHORITY || 'http://localhost:5556/dex'

// OIDC Configuration
export const oidcConfig: UserManagerSettings = {
  // Authority (issuer URL) - must match JWT issuer claim
  authority,

  // Client configuration
  client_id: import.meta.env.VITE_OIDC_CLIENT_ID || 'wip-console',
  client_secret: import.meta.env.VITE_OIDC_CLIENT_SECRET || 'wip-console-secret',

  // Redirect URIs
  redirect_uri: `${window.location.origin}/auth/callback`,
  post_logout_redirect_uri: `${window.location.origin}/`,
  silent_redirect_uri: `${window.location.origin}/auth/silent-renew`,

  // OAuth2 settings
  response_type: 'code',
  scope: 'openid profile email',

  // Token handling
  automaticSilentRenew: false, // Disable - requires iframe which can have issues
  includeIdTokenInSilentRenew: true,

  // User info
  loadUserInfo: true,
}

// Check if OIDC is configured (authority is set)
export const isOidcEnabled = (): boolean => {
  return !!oidcConfig.authority
}

// Storage keys
export const AUTH_STORAGE_KEYS = {
  API_KEY: 'wip-console-api-key',
  AUTH_MODE: 'wip-console-auth-mode',
} as const

// Auth modes
export type AuthMode = 'none' | 'api_key' | 'oidc'
