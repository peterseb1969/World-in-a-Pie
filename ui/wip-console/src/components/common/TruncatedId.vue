<script setup lang="ts">
import { ref, computed } from 'vue'
import Button from 'primevue/button'

const props = withDefaults(defineProps<{
  id: string
  length?: number
  showCopy?: boolean
}>(), {
  length: 8,
  showCopy: true
})

const copied = ref(false)

const truncated = computed(() => {
  if (!props.id) return ''
  if (props.id.length <= props.length) return props.id
  return props.id.slice(0, props.length) + '...'
})

async function copyToClipboard() {
  try {
    await navigator.clipboard.writeText(props.id)
    copied.value = true
    setTimeout(() => {
      copied.value = false
    }, 1500)
  } catch (e) {
    console.error('Failed to copy:', e)
  }
}
</script>

<template>
  <span class="truncated-id" v-tooltip.top="id">
    <code class="id-text">{{ truncated }}</code>
    <Button
      v-if="showCopy"
      :icon="copied ? 'pi pi-check' : 'pi pi-copy'"
      :severity="copied ? 'success' : 'secondary'"
      text
      rounded
      size="small"
      class="copy-btn"
      @click.stop="copyToClipboard"
      v-tooltip.top="copied ? 'Copied!' : 'Copy full ID'"
    />
  </span>
</template>

<style scoped>
.truncated-id {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
}

.id-text {
  font-size: 0.85rem;
  padding: 0.15rem 0.35rem;
  background: var(--surface-100);
  border-radius: 4px;
  font-family: monospace;
}

.copy-btn {
  width: 1.5rem !important;
  height: 1.5rem !important;
  padding: 0 !important;
}

.copy-btn :deep(.p-button-icon) {
  font-size: 0.75rem;
}
</style>
