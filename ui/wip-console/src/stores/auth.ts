import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { defStoreClient, templateStoreClient } from '@/api/client'

const STORAGE_KEY = 'wip-console-api-key'

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
    // Set API key for both clients
    defStoreClient.setApiKey(key)
    templateStoreClient.setApiKey(key)
    localStorage.setItem(STORAGE_KEY, key)
  }

  function clearApiKey() {
    apiKey.value = ''
    defStoreClient.setApiKey('')
    templateStoreClient.setApiKey('')
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
