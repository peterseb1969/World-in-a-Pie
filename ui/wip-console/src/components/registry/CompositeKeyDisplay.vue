<script setup lang="ts">
defineProps<{
  compositeKey: Record<string, unknown>
  compact?: boolean
}>()
</script>

<template>
  <div v-if="!compositeKey || Object.keys(compositeKey).length === 0" class="empty-key">
    (empty)
  </div>
  <div v-else :class="['key-display', { compact }]">
    <span
      v-for="(value, key) in compositeKey"
      :key="String(key)"
      class="key-badge"
    >
      <span class="key-name">{{ key }}</span>
      <code class="key-value">{{ value }}</code>
    </span>
  </div>
</template>

<style scoped>
.key-display {
  display: flex;
  flex-wrap: wrap;
  gap: 0.375rem;
}

.key-badge {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  padding: 0.125rem 0.5rem;
  background: var(--p-surface-50);
  border: 1px solid var(--p-surface-200);
  border-radius: 4px;
  font-size: 0.8125rem;
}

.compact .key-badge {
  padding: 0.0625rem 0.375rem;
  font-size: 0.75rem;
}

.key-name {
  color: var(--p-text-muted-color);
  font-weight: 500;
}

.key-value {
  font-family: var(--font-monospace, monospace);
  font-size: inherit;
}

.empty-key {
  color: var(--p-text-muted-color);
  font-style: italic;
  font-size: 0.8125rem;
}
</style>
