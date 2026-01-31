import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { UserManager, User } from 'oidc-client-ts'
import { oidcConfig, AUTH_STORAGE_KEYS, type AuthMode } from '@/config/auth'
import { defStoreClient, templateStoreClient, documentStoreClient } from '@/api/client'

export const useAuthStore = defineStore('auth', () => {
  // State
  const authMode = ref<AuthMode>('none')
  const apiKey = ref<string>('')
  const oidcUser = ref<User | null>(null)
  const isLoading = ref(false)
  const error = ref<string | null>(null)

  // OIDC UserManager (lazy initialized)
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
      return oidcUser.value !== null && !oidcUser.value.expired
    }
    return false
  })

  const currentUser = computed(() => {
    if (authMode.value === 'oidc' && oidcUser.value) {
      return {
        email: oidcUser.value.profile.email || '',
        name: oidcUser.value.profile.name || oidcUser.value.profile.preferred_username || '',
        sub: oidcUser.value.profile.sub,
      }
    }
    return null
  })

  const accessToken = computed(() => {
    if (authMode.value === 'oidc' && oidcUser.value && !oidcUser.value.expired) {
      return oidcUser.value.access_token
    }
    return null
  })

  // Update API clients with current auth
  function updateClients() {
    if (authMode.value === 'api_key') {
      defStoreClient.setAuth({ type: 'api_key', value: apiKey.value })
      templateStoreClient.setAuth({ type: 'api_key', value: apiKey.value })
      documentStoreClient.setAuth({ type: 'api_key', value: apiKey.value })
    } else if (authMode.value === 'oidc' && oidcUser.value) {
      defStoreClient.setAuth({ type: 'bearer', value: oidcUser.value.access_token })
      templateStoreClient.setAuth({ type: 'bearer', value: oidcUser.value.access_token })
      documentStoreClient.setAuth({ type: 'bearer', value: oidcUser.value.access_token })
    } else {
      defStoreClient.setAuth(null)
      templateStoreClient.setAuth(null)
      documentStoreClient.setAuth(null)
    }
  }

  // Initialize from storage
  async function initialize() {
    const storedMode = localStorage.getItem(AUTH_STORAGE_KEYS.AUTH_MODE) as AuthMode | null
    const storedApiKey = localStorage.getItem(AUTH_STORAGE_KEYS.API_KEY)

    if (storedMode === 'api_key' && storedApiKey) {
      authMode.value = 'api_key'
      apiKey.value = storedApiKey
      updateClients()
    } else if (storedMode === 'oidc') {
      // Try to restore OIDC session
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

  // OIDC authentication
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

  function clearOidcUser() {
    oidcUser.value = null
    if (authMode.value === 'oidc') {
      authMode.value = 'none'
      localStorage.removeItem(AUTH_STORAGE_KEYS.AUTH_MODE)
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
    isLoading,
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
    handleOidcCallback,
    handleSilentRenewCallback,
    logoutOidc,
    logout,
  }
})
