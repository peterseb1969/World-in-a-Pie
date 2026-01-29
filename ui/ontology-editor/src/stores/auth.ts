import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { apiClient } from '@/api/client'

const STORAGE_KEY = 'def-store-api-key'

export const useAuthStore = defineStore('auth', () => {
  const apiKey = ref<string>('')

  const isAuthenticated = computed(() => apiKey.value.length > 0)

  function initialize() {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored) {
      setApiKey(stored)
    }
  }

  function setApiKey(key: string) {
    apiKey.value = key
    apiClient.setApiKey(key)
    localStorage.setItem(STORAGE_KEY, key)
  }

  function clearApiKey() {
    apiKey.value = ''
    apiClient.setApiKey('')
    localStorage.removeItem(STORAGE_KEY)
  }

  return {
    apiKey,
    isAuthenticated,
    initialize,
    setApiKey,
    clearApiKey
  }
})
