import { defineStore } from 'pinia'
import { ref } from 'vue'
import { defStoreClient } from '@/api/client'
import type {
  Term,
  CreateTermRequest,
  UpdateTermRequest,
  DeprecateTermRequest,
  BulkCreateTermRequest,
  BulkOperationResponse
} from '@/types'

export const useTermStore = defineStore('term', () => {
  const terms = ref<Term[]>([])
  const currentTerm = ref<Term | null>(null)
  const total = ref(0)
  const terminologyId = ref<string | null>(null)
  const terminologyCode = ref<string | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetchTerms(termId: string, params?: {
    status?: string
    search?: string
  }) {
    loading.value = true
    error.value = null
    try {
      const response = await defStoreClient.listTerms(termId, params)
      terms.value = response.items
      total.value = response.total
      terminologyId.value = response.terminology_id
      terminologyCode.value = response.terminology_code
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch terms'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function fetchTerm(termId: string) {
    loading.value = true
    error.value = null
    try {
      currentTerm.value = await defStoreClient.getTerm(termId)
      return currentTerm.value
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch term'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function createTerm(termologyId: string, data: CreateTermRequest) {
    loading.value = true
    error.value = null
    try {
      const created = await defStoreClient.createTerm(termologyId, data)
      terms.value.push(created)
      total.value++
      return created
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to create term'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function updateTerm(termId: string, data: UpdateTermRequest) {
    loading.value = true
    error.value = null
    try {
      const updated = await defStoreClient.updateTerm(termId, data)
      const index = terms.value.findIndex(t => t.term_id === termId)
      if (index !== -1) {
        terms.value[index] = updated
      }
      if (currentTerm.value?.term_id === termId) {
        currentTerm.value = updated
      }
      return updated
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to update term'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function deprecateTerm(termId: string, data: DeprecateTermRequest) {
    loading.value = true
    error.value = null
    try {
      const updated = await defStoreClient.deprecateTerm(termId, data)
      const index = terms.value.findIndex(t => t.term_id === termId)
      if (index !== -1) {
        terms.value[index] = updated
      }
      if (currentTerm.value?.term_id === termId) {
        currentTerm.value = updated
      }
      return updated
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to deprecate term'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function deleteTerm(termId: string) {
    loading.value = true
    error.value = null
    try {
      await defStoreClient.deleteTerm(termId)
      terms.value = terms.value.filter(t => t.term_id !== termId)
      total.value--
      if (currentTerm.value?.term_id === termId) {
        currentTerm.value = null
      }
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to delete term'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function bulkCreateTerms(
    termologyId: string,
    data: BulkCreateTermRequest
  ): Promise<BulkOperationResponse> {
    loading.value = true
    error.value = null
    try {
      const result = await defStoreClient.bulkCreateTerms(termologyId, data)
      // Refresh the terms list after bulk create
      await fetchTerms(termologyId)
      return result
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to bulk create terms'
      throw e
    } finally {
      loading.value = false
    }
  }

  function clearTerms() {
    terms.value = []
    total.value = 0
    terminologyId.value = null
    terminologyCode.value = null
  }

  function clearCurrent() {
    currentTerm.value = null
  }

  return {
    terms,
    currentTerm,
    total,
    terminologyId,
    terminologyCode,
    loading,
    error,
    fetchTerms,
    fetchTerm,
    createTerm,
    updateTerm,
    deprecateTerm,
    deleteTerm,
    bulkCreateTerms,
    clearTerms,
    clearCurrent
  }
})
