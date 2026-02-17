<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import Button from 'primevue/button'

const props = defineProps<{
  entityType: string
  entryId: string
}>()

const router = useRouter()

const routeInfo = computed(() => {
  switch (props.entityType) {
    case 'terminologies':
      return { name: 'terminology-detail', params: { id: props.entryId } }
    case 'templates':
      return { name: 'template-detail', params: { id: props.entryId } }
    case 'documents':
      return { name: 'document-detail', params: { id: props.entryId } }
    case 'files':
      return { name: 'file-detail', params: { id: props.entryId } }
    default:
      return null
  }
})

const icon = computed(() => {
  switch (props.entityType) {
    case 'terminologies': return 'pi pi-book'
    case 'terms': return 'pi pi-tag'
    case 'templates': return 'pi pi-file'
    case 'documents': return 'pi pi-folder'
    case 'files': return 'pi pi-images'
    default: return 'pi pi-circle'
  }
})

function navigate() {
  if (routeInfo.value) {
    router.push(routeInfo.value)
  }
}
</script>

<template>
  <Button
    v-if="routeInfo"
    :icon="icon"
    :label="`View ${entityType.slice(0, -1)}`"
    text
    size="small"
    @click="navigate"
  />
  <span v-else class="no-link">
    <i :class="icon"></i>
    {{ entityType }}
  </span>
</template>

<style scoped>
.no-link {
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
  font-size: 0.8125rem;
  color: var(--p-text-muted-color);
}
.no-link i {
  font-size: 0.75rem;
}
</style>
