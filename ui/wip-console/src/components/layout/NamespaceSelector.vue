<script setup lang="ts">
import { computed, onMounted } from 'vue'
import Dropdown from 'primevue/dropdown'
import Tag from 'primevue/tag'
import { useNamespaceStore } from '@/stores'

const namespaceStore = useNamespaceStore()

// Load groups on mount
onMounted(() => {
  namespaceStore.loadGroups()
})

// Filter to only active groups, ensure wip is always first
const activeGroups = computed(() => {
  const groups = namespaceStore.groups.filter(g => g.status === 'active')

  // Sort: wip first, then alphabetically (use slice to avoid mutating)
  return [...groups].sort((a, b) => {
    if (a.prefix === 'wip') return -1
    if (b.prefix === 'wip') return 1
    return a.prefix.localeCompare(b.prefix)
  })
})

// Current group for v-model
const selectedGroup = computed({
  get: () => namespaceStore.currentGroup,
  set: (value: string) => namespaceStore.setCurrentGroup(value)
})

// Severity for non-production namespaces
function getSeverity(prefix: string): 'success' | 'info' | 'warn' | 'danger' | 'secondary' | 'contrast' | undefined {
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
