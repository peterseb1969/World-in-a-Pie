<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import Dropdown from 'primevue/dropdown'
import Tag from 'primevue/tag'
import { useNamespaceStore, useAuthStore } from '@/stores'

const namespaceStore = useNamespaceStore()
const authStore = useAuthStore()

// Load namespaces on mount (only if authenticated)
onMounted(() => {
  if (authStore.isAuthenticated) {
    namespaceStore.loadNamespaces()
  }
})

// Reload namespaces when auth state changes (e.g., after login)
watch(() => authStore.isAuthenticated, (isAuth) => {
  if (isAuth) {
    namespaceStore.loadNamespaces()
  }
})

// Synthetic "All" entry for unfiltered view (only shows data from accessible namespaces)
const allEntry = { prefix: 'all', description: 'All accessible namespaces', status: 'active' as const }

// Show only accessible namespaces + "all" option
const activeGroups = computed(() => {
  const accessible = namespaceStore.accessibleNamespaces || []
  if (accessible.length === 0) return []

  // Sort: wip first, then alphabetically
  const sorted = [...accessible].sort((a, b) => {
    if (a.prefix === 'wip') return -1
    if (b.prefix === 'wip') return 1
    return a.prefix.localeCompare(b.prefix)
  })

  // Only show "all" if there are multiple namespaces
  if (sorted.length > 1) {
    return [allEntry, ...sorted]
  }
  return sorted
})

// Current namespace for v-model
const selectedGroup = computed({
  get: () => namespaceStore.current,
  set: (value: string) => namespaceStore.setCurrent(value)
})

// Severity for non-production namespaces
function getSeverity(prefix: string): 'success' | 'info' | 'warn' | 'danger' | 'secondary' | 'contrast' | undefined {
  if (prefix === 'all') return 'contrast'
  if (prefix === 'wip') return undefined
  if (prefix.startsWith('dev')) return 'info'
  if (prefix.startsWith('test') || prefix.startsWith('staging')) return 'warn'
  return 'secondary'
}

// Permission badge for each namespace
function getPermissionIcon(prefix: string): string {
  if (prefix === 'all') return ''
  const ns = namespaceStore.accessibleNamespaces.find(n => n.prefix === prefix)
  if (!ns) return ''
  if (ns.permission === 'admin') return 'pi pi-shield'
  if (ns.permission === 'write') return 'pi pi-pencil'
  return 'pi pi-eye'
}
</script>

<template>
  <div class="namespace-selector">
    <Dropdown
      v-model="selectedGroup"
      :options="activeGroups"
      optionLabel="prefix"
      optionValue="prefix"
      placeholder="Namespace"
      class="namespace-dropdown"
      :loading="namespaceStore.loading"
    >
      <template #value="slotProps">
        <div v-if="slotProps.value" class="namespace-value">
          <Tag
            :value="slotProps.value.toUpperCase()"
            :severity="getSeverity(slotProps.value)"
          />
          <i v-if="getPermissionIcon(slotProps.value)" :class="getPermissionIcon(slotProps.value)" class="permission-icon" />
        </div>
        <span v-else>{{ slotProps.placeholder }}</span>
      </template>
      <template #option="slotProps">
        <div class="namespace-option">
          <Tag
            :value="slotProps.option.prefix.toUpperCase()"
            :severity="getSeverity(slotProps.option.prefix)"
          />
          <span class="namespace-description">{{ slotProps.option.description }}</span>
          <i v-if="getPermissionIcon(slotProps.option.prefix)" :class="getPermissionIcon(slotProps.option.prefix)" class="permission-icon" />
        </div>
      </template>
    </Dropdown>
  </div>
</template>

<style scoped>
.namespace-selector {
  display: flex;
  align-items: center;
}

.namespace-dropdown {
  min-width: 120px;
}

.namespace-value {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.namespace-option {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.namespace-description {
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
}

.permission-icon {
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
}
</style>
