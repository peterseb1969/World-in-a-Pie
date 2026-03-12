import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { registryClient, type IdAlgorithmConfig } from '@/api/client'

/**
 * User-facing namespace for organizing data.
 *
 * Users work with Namespaces (e.g., "wip", "dev", "prod"). Each namespace
 * has configurable ID generation per entity type.
 */
export interface Namespace {
  prefix: string
  description: string
  isolation_mode: 'open' | 'strict'
  allowed_external_refs: string[]
  id_config: Record<string, unknown>
  status: 'active' | 'archived' | 'deleted'
  created_at: string
  created_by: string | null
  updated_at: string
  updated_by: string | null
}

export interface NamespaceStats {
  prefix: string
  description: string
  isolation_mode: string
  status: string
  entity_counts: Record<string, number>
}

const STORAGE_KEY = 'wip-namespace'

export const useNamespaceStore = defineStore('namespace', () => {
  // State
  const namespaces = ref<Namespace[]>([])
  const current = ref<string>(localStorage.getItem(STORAGE_KEY) || 'wip')
  const loading = ref(false)
  const error = ref<string | null>(null)

  // "all" is a special value meaning no namespace filtering
  const isAll = computed(() => current.value === 'all')

  // Returns the current namespace string for API params, or undefined for "all"
  const currentNamespaceParam = computed(() => isAll.value ? undefined : current.value)

  // Current namespace object
  const currentNamespace = computed(() =>
    namespaces.value.find(ns => ns.prefix === current.value) || null
  )

  // Actions
  async function loadNamespaces() {
    loading.value = true
    error.value = null
    try {
      namespaces.value = await registryClient.listNamespaces()
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to load namespaces'
      console.error('Failed to load namespaces:', e)
    } finally {
      loading.value = false
    }
  }

  function setCurrent(prefix: string) {
    current.value = prefix
    localStorage.setItem(STORAGE_KEY, prefix)
  }

  async function createNamespace(data: {
    prefix: string
    description?: string
    isolation_mode?: 'open' | 'strict'
    id_config?: Record<string, IdAlgorithmConfig>
    created_by?: string
  }): Promise<Namespace> {
    loading.value = true
    error.value = null
    try {
      const ns = await registryClient.createNamespace(data)
      await loadNamespaces()
      return ns
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to create namespace'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function archiveNamespace(prefix: string, archivedBy?: string): Promise<Namespace> {
    loading.value = true
    error.value = null
    try {
      const ns = await registryClient.archiveNamespace(prefix, archivedBy)
      const index = namespaces.value.findIndex(n => n.prefix === prefix)
      if (index !== -1) {
        namespaces.value[index] = ns
      }
      if (current.value === prefix) {
        setCurrent('wip')
      }
      return ns
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to archive namespace'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function restoreNamespace(prefix: string, restoredBy?: string): Promise<Namespace> {
    loading.value = true
    error.value = null
    try {
      const ns = await registryClient.restoreNamespace(prefix, restoredBy)
      const index = namespaces.value.findIndex(n => n.prefix === prefix)
      if (index !== -1) {
        namespaces.value[index] = ns
      }
      return ns
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to restore namespace'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function updateNamespace(
    prefix: string,
    data: {
      description?: string
      isolation_mode?: 'open' | 'strict'
      id_config?: Record<string, IdAlgorithmConfig>
      updated_by?: string
    }
  ): Promise<Namespace> {
    loading.value = true
    error.value = null
    try {
      const ns = await registryClient.updateNamespace(prefix, data)
      const index = namespaces.value.findIndex(n => n.prefix === prefix)
      if (index !== -1) {
        namespaces.value[index] = ns
      }
      return ns
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to update namespace'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function getNamespaceStats(prefix: string): Promise<NamespaceStats> {
    return await registryClient.getNamespaceStats(prefix)
  }

  async function getIdConfig(prefix: string): Promise<Record<string, unknown>> {
    return await registryClient.getIdConfig(prefix)
  }

  return {
    // State
    namespaces,
    current,
    loading,
    error,
    // Computed
    currentNamespaceParam,
    currentNamespace,
    isAll,
    // Actions
    loadNamespaces,
    setCurrent,
    createNamespace,
    updateNamespace,
    archiveNamespace,
    restoreNamespace,
    getNamespaceStats,
    getIdConfig,
  }
})
