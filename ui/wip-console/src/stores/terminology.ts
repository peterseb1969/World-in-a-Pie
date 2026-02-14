import { defineStore } from 'pinia'
import { ref, watch, computed } from 'vue'
import { defStoreClient } from '@/api/client'
import { useNamespaceStore } from './namespace'
import type {
  Terminology,
  CreateTerminologyRequest,
  UpdateTerminologyRequest
} from '@/types'

// Extended terminology with pool info for UI display
export interface TerminologyWithPool extends Terminology {
  _poolId: string
  _isExternal: boolean
}

export const useTerminologyStore = defineStore('terminology', () => {
  const namespaceStore = useNamespaceStore()
  const ownTerminologies = ref<Terminology[]>([])
  const wipTerminologies = ref<Terminology[]>([])
  const currentTerminology = ref<Terminology | null>(null)
  const total = ref(0)
  const wipTotal = ref(0)
  const loading = ref(false)
  const error = ref<string | null>(null)

  // Combined list for backward compatibility
  const terminologies = computed(() => ownTerminologies.value)

  // All terminologies with pool info (own first, then WIP)
  const allTerminologies = computed<TerminologyWithPool[]>(() => {
    const own = ownTerminologies.value.map(t => ({
      ...t,
      _poolId: namespaceStore.terminologiesPool ?? 'all',
      _isExternal: false
    }))
    const wip = wipTerminologies.value.map(t => ({
      ...t,
      _poolId: 'wip-terminologies',
      _isExternal: true
    }))
    return [...own, ...wip]
  })

  // Should we show WIP section? Only for open namespaces that are not WIP or "all"
  const showWipSection = computed(() => {
    if (namespaceStore.isAll) return false
    const group = namespaceStore.currentNamespace
    const isWip = namespaceStore.current === 'wip'
    const isOpen = !group || group.isolation_mode === 'open'
    return isOpen && !isWip
  })

  // Watch for namespace changes and refetch
  watch(() => namespaceStore.terminologiesPool, () => {
    fetchTerminologies()
  })

  async function fetchTerminologies(params?: {
    page?: number
    page_size?: number
    status?: string
    search?: string
  }) {
    loading.value = true
    error.value = null
    try {
      // Fetch own namespace
      const ownResponse = await defStoreClient.listTerminologies({
        ...params,
        pool_id: namespaceStore.terminologiesPool
      })
      ownTerminologies.value = ownResponse.items
      total.value = ownResponse.total

      // If open mode and not already WIP, also fetch WIP terminologies
      if (showWipSection.value) {
        try {
          const wipResponse = await defStoreClient.listTerminologies({
            ...params,
            pool_id: 'wip-terminologies'
          })
          wipTerminologies.value = wipResponse.items
          wipTotal.value = wipResponse.total
        } catch {
          // WIP fetch failed - not critical, continue with own data
          wipTerminologies.value = []
          wipTotal.value = 0
        }
      } else {
        wipTerminologies.value = []
        wipTotal.value = 0
      }
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch terminologies'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function fetchTerminology(id: string) {
    loading.value = true
    error.value = null
    try {
      currentTerminology.value = await defStoreClient.getTerminology(id)
      return currentTerminology.value
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch terminology'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function createTerminology(data: CreateTerminologyRequest) {
    loading.value = true
    error.value = null
    try {
      const created = await defStoreClient.createTerminology(data)
      terminologies.value.unshift(created)
      total.value++
      return created
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to create terminology'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function updateTerminology(id: string, data: UpdateTerminologyRequest) {
    loading.value = true
    error.value = null
    try {
      const updated = await defStoreClient.updateTerminology(id, data)
      const index = terminologies.value.findIndex(t => t.terminology_id === id)
      if (index !== -1) {
        terminologies.value[index] = updated
      }
      if (currentTerminology.value?.terminology_id === id) {
        currentTerminology.value = updated
      }
      return updated
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to update terminology'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function deleteTerminology(id: string) {
    loading.value = true
    error.value = null
    try {
      await defStoreClient.deleteTerminology(id)
      ownTerminologies.value = ownTerminologies.value.filter(t => t.terminology_id !== id)
      total.value--
      if (currentTerminology.value?.terminology_id === id) {
        currentTerminology.value = null
      }
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to delete terminology'
      throw e
    } finally {
      loading.value = false
    }
  }

  function clearCurrent() {
    currentTerminology.value = null
  }

  return {
    // Data
    terminologies,
    ownTerminologies,
    wipTerminologies,
    allTerminologies,
    currentTerminology,
    total,
    wipTotal,
    loading,
    error,
    // Computed
    showWipSection,
    // Actions
    fetchTerminologies,
    fetchTerminology,
    createTerminology,
    updateTerminology,
    deleteTerminology,
    clearCurrent
  }
})
