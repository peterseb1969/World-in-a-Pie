import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { templateStoreClient, defStoreClient } from '@/api/client'
import { useNamespaceStore } from './namespace'
import type {
  Template,
  CreateTemplateRequest,
  UpdateTemplateRequest,
  ValidateTemplateResponse,
  Terminology,
  BulkResultItem
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

  // Should we show WIP section? Only for open namespaces that are not WIP or "all"
  const showWipSection = computed(() => {
    if (namespaceStore.isAll) return false
    const group = namespaceStore.currentNamespace
    const isWip = namespaceStore.current === 'wip'
    const isOpen = !group || group.isolation_mode === 'open'
    return isOpen && !isWip
  })

  // Expose namespace param for components to watch
  const namespaceParam = computed(() => namespaceStore.currentNamespaceParam)

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
        namespace: namespaceStore.currentNamespaceParam
      })
      ownTemplates.value = ownResponse.items
      total.value = ownResponse.total

      // If open mode and not already WIP, also fetch WIP templates
      if (showWipSection.value) {
        try {
          const wipResponse = await templateStoreClient.listTemplates({
            ...params,
            namespace: 'wip'
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

  async function fetchTemplateWithRaw(id: string, version?: number) {
    loading.value = true
    error.value = null
    try {
      const [resolved, raw] = await Promise.all([
        templateStoreClient.getTemplate(id, version),
        templateStoreClient.getTemplateRaw(id, version)
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
      const ns = data.namespace ?? namespaceStore.currentNamespaceParam
      if (!ns) {
        throw new Error('Namespace is required to create a template. Please select a namespace (not "all").')
      }
      const payload = { ...data, namespace: ns }
      const result = await templateStoreClient.createTemplate(payload)
      const created = await templateStoreClient.getTemplate(result.id!)
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

  async function updateTemplate(id: string, data: UpdateTemplateRequest): Promise<BulkResultItem> {
    loading.value = true
    error.value = null
    try {
      const result = await templateStoreClient.updateTemplate(id, data)
      // Only update the list if a new version was created
      if (result.is_new_version) {
        // Fetch the new template to add to the list
        const newTemplate = await templateStoreClient.getTemplate(result.id!)
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

  async function deleteTemplate(id: string, version?: number, force?: boolean) {
    loading.value = true
    error.value = null
    try {
      const opts: { version?: number; force?: boolean } = {}
      if (version) opts.version = version
      if (force) opts.force = true
      await templateStoreClient.deleteTemplate(id, Object.keys(opts).length ? opts : undefined)
      if (version) {
        // Version-specific deactivation: update the version's status in the local list
        const idx = ownTemplates.value.findIndex(t => t.template_id === id && t.version === version)
        if (idx >= 0) {
          ownTemplates.value[idx] = { ...ownTemplates.value[idx], status: 'inactive' }
        }
      } else {
        // Full deactivation: remove from list
        ownTemplates.value = ownTemplates.value.filter(t => t.template_id !== id)
        total.value--
      }
      if (currentTemplate.value?.template_id === id && (!version || currentTemplate.value.version === version)) {
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
      const template = await templateStoreClient.getTemplateByValueAndVersion(code, version)
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
        namespace: namespaceStore.currentNamespaceParam
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
    namespaceParam,
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
