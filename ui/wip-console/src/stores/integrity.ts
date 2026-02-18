import { ref } from 'vue'
import { defineStore } from 'pinia'
import { reportingSyncClient, type IntegrityCheckResult } from '@/api/client'

export const useIntegrityStore = defineStore('integrity', () => {
  const result = ref<IntegrityCheckResult | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function run(params: {
    document_limit?: number
    check_term_refs?: boolean
    recent_first?: boolean
  }) {
    loading.value = true
    error.value = null

    try {
      result.value = await reportingSyncClient.getIntegrityCheck(params)
    } catch (e) {
      console.error('Failed to load integrity check:', e)
      error.value = e instanceof Error ? e.message : 'Unknown error'
      result.value = null
    } finally {
      loading.value = false
    }
  }

  function clear() {
    result.value = null
    error.value = null
  }

  return { result, loading, error, run, clear }
})
