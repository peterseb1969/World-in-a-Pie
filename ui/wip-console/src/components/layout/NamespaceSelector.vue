<script setup lang="ts">
import { computed, onMounted } from 'vue'
import Dropdown from 'primevue/dropdown'
import Tag from 'primevue/tag'
import { useNamespaceStore } from '@/stores'

const namespaceStore = useNamespaceStore()

// Load namespaces on mount
onMounted(() => {
  namespaceStore.loadNamespaces()
})

// Synthetic "All" entry for unfiltered view
const allEntry = { prefix: 'all', description: 'All namespaces (no filter)', status: 'active' as const }

// Filter to only active namespaces, ensure "All" first, then wip, then alphabetically
const activeGroups = computed(() => {
  const namespaces = namespaceStore.namespaces || []
  const active = namespaces.filter(ns => ns.status === 'active')

  // Sort: wip first, then alphabetically
  const sorted = [...active].sort((a, b) => {
    if (a.prefix === 'wip') return -1
    if (b.prefix === 'wip') return 1
    return a.prefix.localeCompare(b.prefix)
  })

  return [allEntry, ...sorted]
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
</style>
