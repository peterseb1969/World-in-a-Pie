import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { registryClient } from '@/api/client'

export interface NamespaceGroup {
  prefix: string
  description: string
  isolation_mode: 'open' | 'strict'
  allowed_external_refs: string[]
  status: 'active' | 'archived' | 'deleted'
  created_at: string
  created_by: string | null
  updated_at: string
  updated_by: string | null
  terminologies_ns: string
  terms_ns: string
  templates_ns: string
  documents_ns: string
  files_ns: string
}

export interface NamespaceGroupStats {
  prefix: string
  description: string
  isolation_mode: string
  status: string
  namespaces: Record<string, number>
}

const STORAGE_KEY = 'wip-namespace-group'

export const useNamespaceStore = defineStore('namespace', () => {
  // State
  const groups = ref<NamespaceGroup[]>([])
  const currentGroup = ref<string>(localStorage.getItem(STORAGE_KEY) || 'wip')
  const loading = ref(false)
  const error = ref<string | null>(null)

  // Computed - derive namespace IDs from current group
  const terminologiesNs = computed(() => `${currentGroup.value}-terminologies`)
  const termsNs = computed(() => `${currentGroup.value}-terms`)
  const templatesNs = computed(() => `${currentGroup.value}-templates`)
  const documentsNs = computed(() => `${currentGroup.value}-documents`)
  const filesNs = computed(() => `${currentGroup.value}-files`)

  // Current group object
  const currentGroupData = computed(() =>
    groups.value.find(g => g.prefix === currentGroup.value) || null
  )

  // Is non-production namespace?
  const isNonProduction = computed(() => currentGroup.value !== 'wip')

  // Actions
  async function loadGroups() {
    loading.value = true
    error.value = null
    try {
      groups.value = await registryClient.listNamespaceGroups()
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to load namespace groups'
      // Don't throw - just log and continue with empty groups
      console.error('Failed to load namespace groups:', e)
    } finally {
      loading.value = false
    }
  }

  function setCurrentGroup(prefix: string) {
    currentGroup.value = prefix
    localStorage.setItem(STORAGE_KEY, prefix)
  }

  async function createGroup(data: {
    prefix: string
    description?: string
    isolation_mode?: 'open' | 'strict'
    created_by?: string
  }): Promise<NamespaceGroup> {
    loading.value = true
    error.value = null
    try {
      const group = await registryClient.createNamespaceGroup(data)
      // Reload all groups to ensure consistency
      await loadGroups()
      return group
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to create namespace group'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function archiveGroup(prefix: string, archivedBy?: string): Promise<NamespaceGroup> {
    loading.value = true
    error.value = null
    try {
      const group = await registryClient.archiveNamespaceGroup(prefix, archivedBy)
      const index = groups.value.findIndex(g => g.prefix === prefix)
      if (index !== -1) {
        groups.value[index] = group
      }
      // If archived group is current, switch to wip
      if (currentGroup.value === prefix) {
        setCurrentGroup('wip')
      }
      return group
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to archive namespace group'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function restoreGroup(prefix: string, restoredBy?: string): Promise<NamespaceGroup> {
    loading.value = true
    error.value = null
    try {
      const group = await registryClient.restoreNamespaceGroup(prefix, restoredBy)
      const index = groups.value.findIndex(g => g.prefix === prefix)
      if (index !== -1) {
        groups.value[index] = group
      }
      return group
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to restore namespace group'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function getGroupStats(prefix: string): Promise<NamespaceGroupStats> {
    return await registryClient.getNamespaceGroupStats(prefix)
  }

  return {
    // State
    groups,
    currentGroup,
    loading,
    error,
    // Computed
    terminologiesNs,
    termsNs,
    templatesNs,
    documentsNs,
    filesNs,
    currentGroupData,
    isNonProduction,
    // Actions
    loadGroups,
    setCurrentGroup,
    createGroup,
    archiveGroup,
    restoreGroup,
    getGroupStats
  }
})
