import { defineStore } from 'pinia'
import { ref } from 'vue'
import { templateStoreClient, defStoreClient } from '@/api/client'
import type {
  Template,
  CreateTemplateRequest,
  UpdateTemplateRequest,
  TemplateUpdateResponse,
  ValidateTemplateResponse,
  Terminology
} from '@/types'

export const useTemplateStore = defineStore('template', () => {
  const templates = ref<Template[]>([])
  const currentTemplate = ref<Template | null>(null)
  const currentTemplateRaw = ref<Template | null>(null)
  const templateVersions = ref<Template[]>([])  // Version history for current template
  const total = ref(0)
  const loading = ref(false)
  const error = ref<string | null>(null)

  // For terminology lookups (term field references)
  const terminologies = ref<Terminology[]>([])

  async function fetchTemplates(params?: {
    page?: number
    page_size?: number
    status?: string
    extends?: string
    code?: string
    latest_only?: boolean
  }) {
    loading.value = true
    error.value = null
    try {
      const response = await templateStoreClient.listTemplates(params)
      templates.value = response.items
      total.value = response.total
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch templates'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function fetchTemplate(id: string) {
    loading.value = true
    error.value = null
    try {
      currentTemplate.value = await templateStoreClient.getTemplate(id)
      return currentTemplate.value
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch template'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function fetchTemplateRaw(id: string) {
    loading.value = true
    error.value = null
    try {
      currentTemplateRaw.value = await templateStoreClient.getTemplateRaw(id)
      return currentTemplateRaw.value
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch raw template'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function fetchTemplateWithRaw(id: string) {
    loading.value = true
    error.value = null
    try {
      const [resolved, raw] = await Promise.all([
        templateStoreClient.getTemplate(id),
        templateStoreClient.getTemplateRaw(id)
      ])
      currentTemplate.value = resolved
      currentTemplateRaw.value = raw
      return { resolved, raw }
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch template'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function createTemplate(data: CreateTemplateRequest) {
    loading.value = true
    error.value = null
    try {
      const created = await templateStoreClient.createTemplate(data)
      templates.value.unshift(created)
      total.value++
      return created
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to create template'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function updateTemplate(id: string, data: UpdateTemplateRequest): Promise<TemplateUpdateResponse> {
    loading.value = true
    error.value = null
    try {
      // Note: updateTemplate creates a NEW version with a NEW template_id if changed
      const result = await templateStoreClient.updateTemplate(id, data)
      // Only update the list if a new version was created
      if (result.is_new_version) {
        // Fetch the new template to add to the list
        const newTemplate = await templateStoreClient.getTemplate(result.template_id)
        if (newTemplate) {
          templates.value.unshift(newTemplate)
          total.value++
          currentTemplate.value = newTemplate
          currentTemplateRaw.value = newTemplate
        }
      }
      return result
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to update template'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function deleteTemplate(id: string) {
    loading.value = true
    error.value = null
    try {
      await templateStoreClient.deleteTemplate(id)
      templates.value = templates.value.filter(t => t.template_id !== id)
      total.value--
      if (currentTemplate.value?.template_id === id) {
        currentTemplate.value = null
        currentTemplateRaw.value = null
      }
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to delete template'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function validateTemplate(id: string): Promise<ValidateTemplateResponse> {
    loading.value = true
    error.value = null
    try {
      return await templateStoreClient.validateTemplate(id)
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to validate template'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function getChildren(id: string) {
    loading.value = true
    error.value = null
    try {
      const response = await templateStoreClient.getChildren(id)
      return response.items
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch children'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function getDescendants(id: string) {
    loading.value = true
    error.value = null
    try {
      const response = await templateStoreClient.getDescendants(id)
      return response.items
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch descendants'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function fetchTemplateVersions(code: string) {
    loading.value = true
    error.value = null
    try {
      const response = await templateStoreClient.getTemplateVersions(code)
      templateVersions.value = response.items
      return response.items
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch template versions'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function fetchTemplateByCodeAndVersion(code: string, version: number) {
    loading.value = true
    error.value = null
    try {
      const template = await templateStoreClient.getTemplateByCodeAndVersion(code, version)
      currentTemplate.value = template
      currentTemplateRaw.value = template
      return template
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch template version'
      throw e
    } finally {
      loading.value = false
    }
  }

  // Fetch terminologies from Def-Store for term field references
  async function fetchTerminologies() {
    try {
      const response = await defStoreClient.listTerminologies({ status: 'active', page_size: 100 })
      terminologies.value = response.items
    } catch (e) {
      console.warn('Failed to fetch terminologies:', e)
      terminologies.value = []
    }
  }

  function clearCurrent() {
    currentTemplate.value = null
    currentTemplateRaw.value = null
  }

  return {
    templates,
    currentTemplate,
    currentTemplateRaw,
    templateVersions,
    total,
    loading,
    error,
    terminologies,
    fetchTemplates,
    fetchTemplate,
    fetchTemplateRaw,
    fetchTemplateWithRaw,
    createTemplate,
    updateTemplate,
    deleteTemplate,
    validateTemplate,
    getChildren,
    getDescendants,
    fetchTemplateVersions,
    fetchTemplateByCodeAndVersion,
    fetchTerminologies,
    clearCurrent
  }
})
