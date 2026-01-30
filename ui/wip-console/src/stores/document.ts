import { defineStore } from 'pinia'
import { ref } from 'vue'
import { documentStoreClient, templateStoreClient, defStoreClient } from '@/api/client'
import type {
  Document,
  CreateDocumentRequest,
  DocumentValidationResponse,
  ValidateDocumentRequest,
  DocumentVersionResponse,
  DocumentQueryParams,
  Template,
  Term
} from '@/types'

export const useDocumentStore = defineStore('document', () => {
  const documents = ref<Document[]>([])
  const currentDocument = ref<Document | null>(null)
  const currentTemplate = ref<Template | null>(null)
  const total = ref(0)
  const pages = ref(0)
  const loading = ref(false)
  const error = ref<string | null>(null)

  // Version history for current document
  const versionHistory = ref<DocumentVersionResponse | null>(null)

  // Cache for terminology terms (keyed by terminology_id)
  const termsCache = ref<Record<string, Term[]>>({})

  async function fetchDocuments(params?: DocumentQueryParams) {
    loading.value = true
    error.value = null
    try {
      const response = await documentStoreClient.listDocuments(params)
      documents.value = response.items
      total.value = response.total
      pages.value = response.pages
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch documents'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function fetchDocument(id: string) {
    loading.value = true
    error.value = null
    try {
      currentDocument.value = await documentStoreClient.getDocument(id)
      // Also fetch the template for this document
      if (currentDocument.value) {
        await fetchTemplate(currentDocument.value.template_id)
      }
      return currentDocument.value
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch document'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function fetchTemplate(templateId: string) {
    try {
      currentTemplate.value = await templateStoreClient.getTemplate(templateId)
      return currentTemplate.value
    } catch (e) {
      console.warn('Failed to fetch template:', e)
      currentTemplate.value = null
      throw e
    }
  }

  async function createDocument(data: CreateDocumentRequest) {
    loading.value = true
    error.value = null
    try {
      const created = await documentStoreClient.createDocument(data)
      documents.value.unshift(created)
      total.value++
      return created
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to create document'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function updateDocument(templateId: string, data: Record<string, unknown>) {
    loading.value = true
    error.value = null
    try {
      // Document Store uses upsert - POST with same identity fields creates a new version
      const updated = await documentStoreClient.updateDocument(templateId, data)
      // The updated document may have a new ID (new version), so refresh the list
      const oldId = currentDocument.value?.document_id
      if (oldId) {
        const index = documents.value.findIndex(d => d.document_id === oldId)
        if (index !== -1) {
          documents.value[index] = updated
        }
      }
      currentDocument.value = updated
      return updated
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to update document'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function deleteDocument(id: string) {
    loading.value = true
    error.value = null
    try {
      await documentStoreClient.deleteDocument(id)
      documents.value = documents.value.filter(d => d.document_id !== id)
      total.value--
      if (currentDocument.value?.document_id === id) {
        currentDocument.value = null
        currentTemplate.value = null
      }
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to delete document'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function archiveDocument(id: string) {
    loading.value = true
    error.value = null
    try {
      const archived = await documentStoreClient.archiveDocument(id)
      const index = documents.value.findIndex(d => d.document_id === id)
      if (index !== -1) {
        documents.value[index] = archived
      }
      if (currentDocument.value?.document_id === id) {
        currentDocument.value = archived
      }
      return archived
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to archive document'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function restoreDocument(id: string) {
    loading.value = true
    error.value = null
    try {
      const restored = await documentStoreClient.restoreDocument(id)
      const index = documents.value.findIndex(d => d.document_id === id)
      if (index !== -1) {
        documents.value[index] = restored
      }
      if (currentDocument.value?.document_id === id) {
        currentDocument.value = restored
      }
      return restored
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to restore document'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function validateDocument(data: ValidateDocumentRequest): Promise<DocumentValidationResponse> {
    loading.value = true
    error.value = null
    try {
      return await documentStoreClient.validateDocument(data)
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to validate document'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function fetchVersions(id: string) {
    loading.value = true
    error.value = null
    try {
      versionHistory.value = await documentStoreClient.getVersions(id)
      return versionHistory.value
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch versions'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function fetchVersion(id: string, version: number) {
    loading.value = true
    error.value = null
    try {
      return await documentStoreClient.getVersion(id, version)
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch version'
      throw e
    } finally {
      loading.value = false
    }
  }

  // Fetch terms for a terminology (with caching)
  async function fetchTermsForTerminology(terminologyId: string): Promise<Term[]> {
    if (termsCache.value[terminologyId]) {
      return termsCache.value[terminologyId]
    }

    try {
      const response = await defStoreClient.listTerms(terminologyId, { status: 'active' })
      termsCache.value[terminologyId] = response.items
      return response.items
    } catch (e) {
      console.warn('Failed to fetch terms for terminology:', terminologyId, e)
      return []
    }
  }

  function clearCurrent() {
    currentDocument.value = null
    currentTemplate.value = null
    versionHistory.value = null
  }

  function clearTermsCache() {
    termsCache.value = {}
  }

  return {
    documents,
    currentDocument,
    currentTemplate,
    total,
    pages,
    loading,
    error,
    versionHistory,
    termsCache,
    fetchDocuments,
    fetchDocument,
    fetchTemplate,
    createDocument,
    updateDocument,
    deleteDocument,
    archiveDocument,
    restoreDocument,
    validateDocument,
    fetchVersions,
    fetchVersion,
    fetchTermsForTerminology,
    clearCurrent,
    clearTermsCache
  }
})
