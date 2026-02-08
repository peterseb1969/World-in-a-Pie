import { defineStore } from 'pinia'
import { ref, watch, computed } from 'vue'
import { templateStoreClient, defStoreClient } from '@/api/client'
import { useNamespaceStore } from './namespace'
import type {
  Template,
  CreateTemplateRequest,
  UpdateTemplateRequest,
  TemplateUpdateResponse,
  ValidateTemplateResponse,
  Terminology
} from '@/types'

export const useTemplateStore = defineStore('template', () => {
  const namespaceStore = useNamespaceStore()
  const ownTemplates = ref<Template[]>([])
  const wipTemplates = ref<Template[]>([])
  const currentTemplate = ref<Template | null>(null)
  const currentTemplateRaw = ref<Template | null>(null)
  const templateVersions = ref<Template[]>([])  // Version history for current template
  const total = ref(0)
  const wipTotal = ref(0)
  const loading = ref(false)
  const error = ref<string | null>(null)

  // For terminology lookups (term field references)
  const terminologies = ref<Terminology[]>([])

  // Combined list for backward compatibility
  const templates = computed(() => ownTemplates.value)

  // Should we show WIP section? Only for open namespaces that are not WIP
  const showWipSection = computed(() => {
    const group = namespaceStore.currentData
    const isWip = namespaceStore.current === 'wip'
    const isOpen = !group || group.isolation_mode === 'open'
    return isOpen && !isWip
  })

  // Watch for namespace changes and refetch
  watch(() => namespaceStore.templatesNs, () => {
    fetchTemplates()
  })

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
      // Fetch own namespace
      const ownResponse = await templateStoreClient.listTemplates({
        ...params,
        namespace: namespaceStore.templatesNs
      })
      ownTemplates.value = ownResponse.items
      total.value = ownResponse.total

      // If open mode and not already WIP, also fetch WIP templates
      if (showWipSection.value) {
        try {
          const wipResponse = await templateStoreClient.listTemplates({
            ...params,
            namespace: 'wip-templates'
          })
          wipTemplates.value = wipResponse.items
          wipTotal.value = wipResponse.total
        } catch {
          wipTemplates.value = []
          wipTotal.value = 0
        }
      } else {
        wipTemplates.value = []
        wipTotal.value = 0
      }
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
      const response = await defStoreClient.listTerminologies({
        status: 'active',
        page_size: 100,
        namespace: namespaceStore.terminologiesNs
      })
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
    // Data
    templates,
    ownTemplates,
    wipTemplates,
    currentTemplate,
    currentTemplateRaw,
    templateVersions,
    total,
    wipTotal,
    loading,
    error,
    terminologies,
    // Computed
    showWipSection,
    // Actions
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
