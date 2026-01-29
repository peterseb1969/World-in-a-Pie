import { defineStore } from 'pinia'
import { ref } from 'vue'
import { defStoreClient } from '@/api/client'
import type {
  Terminology,
  CreateTerminologyRequest,
  UpdateTerminologyRequest
} from '@/types'

export const useTerminologyStore = defineStore('terminology', () => {
  const terminologies = ref<Terminology[]>([])
  const currentTerminology = ref<Terminology | null>(null)
  const total = ref(0)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetchTerminologies(params?: {
    page?: number
    page_size?: number
    status?: string
    search?: string
  }) {
    loading.value = true
    error.value = null
    try {
      const response = await defStoreClient.listTerminologies(params)
      terminologies.value = response.items
      total.value = response.total
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
      terminologies.value = terminologies.value.filter(t => t.terminology_id !== id)
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
    terminologies,
    currentTerminology,
    total,
    loading,
    error,
    fetchTerminologies,
    fetchTerminology,
    createTerminology,
    updateTerminology,
    deleteTerminology,
    clearCurrent
  }
})
