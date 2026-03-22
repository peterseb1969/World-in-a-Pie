import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { UserManager, User } from 'oidc-client-ts'
import { oidcConfig, AUTH_STORAGE_KEYS, type AuthMode } from '@/config/auth'
import { defStoreClient, templateStoreClient, documentStoreClient, fileStoreClient, registryClient } from '@/api/client'

// Simple token storage for password grant flow
interface PasswordGrantUser {
  access_token: string
  id_token?: string
  refresh_token?: string
  token_type: string
  expires_at: number
  profile: {
    sub: string
    email?: string
    name?: string
    preferred_username?: string
  }
}

export const useAuthStore = defineStore('auth', () => {
  // State
  const authMode = ref<AuthMode>('none')
  const apiKey = ref<string>('')
  const oidcUser = ref<User | null>(null)
  const passwordGrantUser = ref<PasswordGrantUser | null>(null)
  const isLoading = ref(false)
  const isInitialized = ref(false)
  const error = ref<string | null>(null)

  // OIDC UserManager (lazy initialized) - kept for redirect flow if needed
  let userManager: UserManager | null = null

  function getUserManager(): UserManager {
    if (!userManager) {
      userManager = new UserManager(oidcConfig)

      // Handle token expiring
      userManager.events.addAccessTokenExpiring(() => {
        console.log('[Auth] Access token expiring, will attempt silent renew')
      })

      // Handle token expired
      userManager.events.addAccessTokenExpired(() => {
        console.log('[Auth] Access token expired')
        clearOidcUser()
      })

      // Handle silent renew error
      userManager.events.addSilentRenewError((err) => {
        console.error('[Auth] Silent renew error:', err)
        error.value = 'Session renewal failed. Please log in again.'
      })

      // Handle user loaded (after login or silent renew)
      userManager.events.addUserLoaded((user) => {
        console.log('[Auth] User loaded:', user.profile.email)
        oidcUser.value = user
        updateClients()
      })

      // Handle user unloaded
      userManager.events.addUserUnloaded(() => {
        console.log('[Auth] User unloaded')
        clearOidcUser()
      })
    }
    return userManager
  }

  // Computed
  const isAuthenticated = computed(() => {
    if (authMode.value === 'api_key') {
      return apiKey.value.length > 0
    }
    if (authMode.value === 'oidc') {
      // Check both redirect flow (oidcUser) and password grant flow (passwordGrantUser)
      if (oidcUser.value !== null && !oidcUser.value.expired) {
        return true
      }
      if (passwordGrantUser.value !== null && passwordGrantUser.value.expires_at > Date.now() / 1000) {
        return true
      }
    }
    return false
  })

  const currentUser = computed(() => {
    if (authMode.value === 'oidc') {
      if (oidcUser.value) {
        return {
          email: oidcUser.value.profile.email || '',
          name: oidcUser.value.profile.name || oidcUser.value.profile.preferred_username || '',
          sub: oidcUser.value.profile.sub,
        }
      }
      if (passwordGrantUser.value) {
        return {
          email: passwordGrantUser.value.profile.email || '',
          name: passwordGrantUser.value.profile.name || passwordGrantUser.value.profile.preferred_username || '',
          sub: passwordGrantUser.value.profile.sub,
        }
      }
    }
    return null
  })

  const accessToken = computed(() => {
    if (authMode.value === 'oidc') {
      if (oidcUser.value && !oidcUser.value.expired) {
        return oidcUser.value.access_token
      }
      if (passwordGrantUser.value && passwordGrantUser.value.expires_at > Date.now() / 1000) {
        return passwordGrantUser.value.access_token
      }
    }
    return null
  })

  // Update API clients with current auth
  function updateClients() {
    if (authMode.value === 'api_key') {
      defStoreClient.setAuth({ type: 'api_key', value: apiKey.value })
      templateStoreClient.setAuth({ type: 'api_key', value: apiKey.value })
      documentStoreClient.setAuth({ type: 'api_key', value: apiKey.value })
      fileStoreClient.setAuth({ type: 'api_key', value: apiKey.value })
      registryClient.setAuth({ type: 'api_key', value: apiKey.value })
    } else if (authMode.value === 'oidc') {
      const token = oidcUser.value?.access_token || passwordGrantUser.value?.access_token
      if (token) {
        defStoreClient.setAuth({ type: 'bearer', value: token })
        templateStoreClient.setAuth({ type: 'bearer', value: token })
        documentStoreClient.setAuth({ type: 'bearer', value: token })
        fileStoreClient.setAuth({ type: 'bearer', value: token })
        registryClient.setAuth({ type: 'bearer', value: token })
      } else {
        defStoreClient.setAuth(null)
        templateStoreClient.setAuth(null)
        documentStoreClient.setAuth(null)
        fileStoreClient.setAuth(null)
        registryClient.setAuth(null)
      }
    } else {
      defStoreClient.setAuth(null)
      templateStoreClient.setAuth(null)
      documentStoreClient.setAuth(null)
      fileStoreClient.setAuth(null)
      registryClient.setAuth(null)
    }
  }

  // Initialize from storage
  async function initialize() {
    try {
      const storedMode = localStorage.getItem(AUTH_STORAGE_KEYS.AUTH_MODE) as AuthMode | null
      const storedApiKey = localStorage.getItem(AUTH_STORAGE_KEYS.API_KEY)
      const storedPasswordGrantUser = localStorage.getItem('wip-console-password-grant-user')

      if (storedMode === 'api_key' && storedApiKey) {
        authMode.value = 'api_key'
        apiKey.value = storedApiKey
        updateClients()
      } else if (storedMode === 'oidc') {
        // Try to restore password grant session first
        if (storedPasswordGrantUser) {
          try {
            const user = JSON.parse(storedPasswordGrantUser) as PasswordGrantUser
            if (user.expires_at > Date.now() / 1000) {
              authMode.value = 'oidc'
              passwordGrantUser.value = user
              updateClients()
              // Schedule refresh for remaining lifetime
              const remainingSeconds = user.expires_at - Math.floor(Date.now() / 1000)
              if (user.refresh_token) {
                schedulePasswordRefresh(remainingSeconds)
              }
              return
            } else if (user.refresh_token) {
              // Token expired but we have a refresh token — try to refresh
              passwordGrantUser.value = user
              authMode.value = 'oidc'
              const success = await refreshPasswordGrantToken()
              if (success) {
                return
              }
              // Refresh failed — fall through to clear
              passwordGrantUser.value = null
              authMode.value = 'none'
              localStorage.removeItem('wip-console-password-grant-user')
            } else {
              // Token expired, no refresh token
              localStorage.removeItem('wip-console-password-grant-user')
            }
          } catch (err) {
            console.error('[Auth] Failed to restore password grant session:', err)
            localStorage.removeItem('wip-console-password-grant-user')
          }
        }

        // Try redirect flow session
        try {
          const user = await getUserManager().getUser()
          if (user && !user.expired) {
            authMode.value = 'oidc'
            oidcUser.value = user
            updateClients()
          } else {
            // Session expired, clear storage
            localStorage.removeItem(AUTH_STORAGE_KEYS.AUTH_MODE)
          }
        } catch (err) {
          console.error('[Auth] Failed to restore OIDC session:', err)
          localStorage.removeItem(AUTH_STORAGE_KEYS.AUTH_MODE)
        }
      }
    } finally {
      isInitialized.value = true
    }
  }

  // API Key authentication
  function setApiKey(key: string) {
    apiKey.value = key
    authMode.value = 'api_key'
    localStorage.setItem(AUTH_STORAGE_KEYS.API_KEY, key)
    localStorage.setItem(AUTH_STORAGE_KEYS.AUTH_MODE, 'api_key')
    updateClients()
  }

  function clearApiKey() {
    apiKey.value = ''
    authMode.value = 'none'
    localStorage.removeItem(AUTH_STORAGE_KEYS.API_KEY)
    localStorage.removeItem(AUTH_STORAGE_KEYS.AUTH_MODE)
    updateClients()
  }

  // OIDC authentication (redirect flow - requires HTTPS for PKCE)
  async function loginWithOidc() {
    isLoading.value = true
    error.value = null
    try {
      await getUserManager().signinRedirect()
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Login failed'
      isLoading.value = false
      throw err
    }
  }

  // Password grant flow - works over HTTP (no PKCE required)
  async function loginWithPassword(username: string, password: string) {
    isLoading.value = true
    error.value = null
    try {
      // Get the token endpoint from OIDC config
      // The authority is /dex (proxied), so token endpoint is /dex/token
      const authority = oidcConfig.authority || '/dex'
      const tokenEndpoint = `${authority}/token`

      const params = new URLSearchParams({
        grant_type: 'password',
        username,
        password,
        client_id: oidcConfig.client_id || 'wip-console',
        client_secret: oidcConfig.client_secret || 'wip-console-secret',
        scope: 'openid profile email groups offline_access',
      })

      const response = await fetch(tokenEndpoint, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: params.toString(),
      })

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(errorData.error_description || errorData.error || 'Login failed')
      }

      const tokenResponse = await response.json()

      // Parse the ID token to get user info (it's a JWT)
      let profile: PasswordGrantUser['profile'] = { sub: 'unknown' }
      if (tokenResponse.id_token) {
        try {
          const payload = tokenResponse.id_token.split('.')[1]
          const decoded = JSON.parse(atob(payload))
          profile = {
            sub: decoded.sub || 'unknown',
            email: decoded.email,
            name: decoded.name,
            preferred_username: decoded.preferred_username,
          }
        } catch (e) {
          console.warn('[Auth] Failed to parse id_token:', e)
        }
      }

      // Calculate expiration time
      const expiresIn = tokenResponse.expires_in || 86400 // Default 24h
      const expiresAt = Math.floor(Date.now() / 1000) + expiresIn

      const user: PasswordGrantUser = {
        access_token: tokenResponse.access_token,
        id_token: tokenResponse.id_token,
        refresh_token: tokenResponse.refresh_token,
        token_type: tokenResponse.token_type || 'Bearer',
        expires_at: expiresAt,
        profile,
      }

      passwordGrantUser.value = user
      authMode.value = 'oidc'

      // Store for session persistence
      localStorage.setItem(AUTH_STORAGE_KEYS.AUTH_MODE, 'oidc')
      localStorage.setItem('wip-console-password-grant-user', JSON.stringify(user))
      localStorage.removeItem(AUTH_STORAGE_KEYS.API_KEY)
      apiKey.value = ''

      updateClients()

      // Schedule automatic refresh if we got a refresh token
      if (tokenResponse.refresh_token) {
        schedulePasswordRefresh(expiresIn)
      }

      return user
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Login failed'
      throw err
    } finally {
      isLoading.value = false
    }
  }

  async function handleOidcCallback(): Promise<User> {
    isLoading.value = true
    error.value = null
    try {
      const user = await getUserManager().signinRedirectCallback()
      oidcUser.value = user
      authMode.value = 'oidc'
      localStorage.setItem(AUTH_STORAGE_KEYS.AUTH_MODE, 'oidc')
      // Clear any stored API key when switching to OIDC
      localStorage.removeItem(AUTH_STORAGE_KEYS.API_KEY)
      apiKey.value = ''
      updateClients()
      return user
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Authentication callback failed'
      throw err
    } finally {
      isLoading.value = false
    }
  }

  async function handleSilentRenewCallback(): Promise<void> {
    try {
      await getUserManager().signinSilentCallback()
    } catch (err) {
      console.error('[Auth] Silent renew callback error:', err)
    }
  }

  // Refresh password grant tokens using refresh_token (M1)
  let passwordRefreshTimer: ReturnType<typeof setTimeout> | null = null

  async function refreshPasswordGrantToken(): Promise<boolean> {
    const user = passwordGrantUser.value
    if (!user?.refresh_token) return false

    try {
      const authority = oidcConfig.authority || '/dex'
      const params = new URLSearchParams({
        grant_type: 'refresh_token',
        refresh_token: user.refresh_token,
        client_id: oidcConfig.client_id || 'wip-console',
        client_secret: oidcConfig.client_secret || 'wip-console-secret',
        scope: 'openid profile email groups offline_access',
      })

      const response = await fetch(`${authority}/token`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: params.toString(),
      })

      if (!response.ok) {
        console.warn('[Auth] Password grant refresh failed:', response.status)
        return false
      }

      const tokenResponse = await response.json()
      const expiresIn = tokenResponse.expires_in || 900
      const expiresAt = Math.floor(Date.now() / 1000) + expiresIn

      passwordGrantUser.value = {
        ...user,
        access_token: tokenResponse.access_token,
        id_token: tokenResponse.id_token || user.id_token,
        refresh_token: tokenResponse.refresh_token || user.refresh_token,
        expires_at: expiresAt,
      }

      localStorage.setItem('wip-console-password-grant-user', JSON.stringify(passwordGrantUser.value))
      updateClients()
      schedulePasswordRefresh(expiresIn)
      console.log('[Auth] Password grant token refreshed, expires in', expiresIn, 's')
      return true
    } catch (err) {
      console.error('[Auth] Password grant refresh error:', err)
      return false
    }
  }

  function schedulePasswordRefresh(expiresIn: number) {
    if (passwordRefreshTimer) clearTimeout(passwordRefreshTimer)
    // Refresh 2 minutes before expiry (or halfway if token is very short-lived)
    const refreshIn = Math.max((expiresIn - 120) * 1000, (expiresIn / 2) * 1000)
    passwordRefreshTimer = setTimeout(async () => {
      const success = await refreshPasswordGrantToken()
      if (!success) {
        console.warn('[Auth] Password grant refresh failed, session will expire')
        error.value = 'Session renewal failed. Please log in again.'
      }
    }, refreshIn)
  }

  function clearOidcUser() {
    oidcUser.value = null
    passwordGrantUser.value = null
    if (passwordRefreshTimer) {
      clearTimeout(passwordRefreshTimer)
      passwordRefreshTimer = null
    }
    if (authMode.value === 'oidc') {
      authMode.value = 'none'
      localStorage.removeItem(AUTH_STORAGE_KEYS.AUTH_MODE)
      localStorage.removeItem('wip-console-password-grant-user')
    }
    updateClients()
  }

  async function logoutOidc() {
    isLoading.value = true
    try {
      clearOidcUser()
      await getUserManager().signoutRedirect()
    } catch (err) {
      console.error('[Auth] Logout error:', err)
      // Still clear local state even if redirect fails
    } finally {
      isLoading.value = false
    }
  }

  // Generic logout (works for both modes)
  async function logout() {
    if (authMode.value === 'oidc') {
      await logoutOidc()
    } else {
      clearApiKey()
    }
  }

  return {
    // State
    authMode,
    apiKey,
    oidcUser,
    passwordGrantUser,
    isLoading,
    isInitialized,
    error,

    // Computed
    isAuthenticated,
    currentUser,
    accessToken,

    // Methods
    initialize,
    setApiKey,
    clearApiKey,
    loginWithOidc,
    loginWithPassword,
    handleOidcCallback,
    handleSilentRenewCallback,
    logoutOidc,
    logout,
  }
})
