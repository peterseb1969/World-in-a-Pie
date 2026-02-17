<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { registryClient, type NamespaceStats } from '@/api/client'

const emit = defineEmits<{
  select: [namespace: string | null]
}>()

const stats = ref<NamespaceStats[]>([])
const loading = ref(false)
const selected = ref<string | null>(null)

onMounted(async () => {
  loading.value = true
  try {
    const namespaces = await registryClient.listNamespaces()
    const results: NamespaceStats[] = []
    for (const ns of namespaces) {
      if (ns.status !== 'active') continue
      try {
        const s = await registryClient.getNamespaceStats(ns.prefix)
        results.push(s)
      } catch {
        // Skip namespace if stats fail
      }
    }
    stats.value = results
  } catch {
    // Silently fail — stats strip is optional
  } finally {
    loading.value = false
  }
})

function totalCount(ns: NamespaceStats): number {
  return Object.values(ns.entity_counts).reduce((sum, c) => sum + c, 0)
}

function toggleSelect(prefix: string) {
  if (selected.value === prefix) {
    selected.value = null
    emit('select', null)
  } else {
    selected.value = prefix
    emit('select', prefix)
  }
}
</script>

<template>
  <div v-if="stats.length > 0" class="stats-strip">
    <div
      v-for="ns in stats"
      :key="ns.prefix"
      :class="['stat-card', { active: selected === ns.prefix }]"
      @click="toggleSelect(ns.prefix)"
    >
      <div class="stat-prefix">{{ ns.prefix }}</div>
      <div class="stat-total">{{ totalCount(ns).toLocaleString() }}</div>
      <div class="stat-breakdown">
        <span v-for="(count, type) in ns.entity_counts" :key="type" class="stat-type">
          {{ type }}: {{ count.toLocaleString() }}
        </span>
      </div>
    </div>
  </div>
  <div v-else-if="loading" class="stats-loading">
    <i class="pi pi-spin pi-spinner"></i>
    Loading namespace stats...
  </div>
</template>

<style scoped>
.stats-strip {
  display: flex;
  gap: 0.75rem;
  overflow-x: auto;
  padding: 0.25rem 0 0.75rem;
}

.stat-card {
  flex: 0 0 auto;
  min-width: 140px;
  padding: 0.625rem 0.875rem;
  background: var(--p-surface-0);
  border: 1px solid var(--p-surface-200);
  border-radius: 8px;
  cursor: pointer;
  transition: border-color 0.15s, box-shadow 0.15s;
}

.stat-card:hover {
  border-color: var(--p-primary-color);
}

.stat-card.active {
  border-color: var(--p-primary-color);
  box-shadow: 0 0 0 1px var(--p-primary-color);
}

.stat-prefix {
  font-weight: 600;
  font-size: 0.8125rem;
  color: var(--p-text-color);
  margin-bottom: 0.25rem;
}

.stat-total {
  font-size: 1.25rem;
  font-weight: 700;
  color: var(--p-primary-color);
  margin-bottom: 0.25rem;
}

.stat-breakdown {
  display: flex;
  flex-direction: column;
  gap: 0.0625rem;
}

.stat-type {
  font-size: 0.6875rem;
  color: var(--p-text-muted-color);
}

.stats-loading {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem;
  color: var(--p-text-muted-color);
  font-size: 0.8125rem;
}
</style>
