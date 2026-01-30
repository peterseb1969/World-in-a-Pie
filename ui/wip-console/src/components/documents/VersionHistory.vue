<script setup lang="ts">
import { computed } from 'vue'
import Timeline from 'primevue/timeline'
import Tag from 'primevue/tag'
import Button from 'primevue/button'
import type { DocumentVersionResponse, DocumentVersionSummary } from '@/types'

const props = defineProps<{
  versionHistory: DocumentVersionResponse | null
  currentVersion?: number
  loading?: boolean
}>()

const emit = defineEmits<{
  'view-version': [version: number]
}>()

// Sort versions in descending order (newest first)
const sortedVersions = computed(() => {
  if (!props.versionHistory) return []
  return [...props.versionHistory.versions].sort((a, b) => b.version - a.version)
})

function getStatusSeverity(status: string): "success" | "info" | "warn" | "danger" | "secondary" | "contrast" | undefined {
  switch (status) {
    case 'active':
      return 'success'
    case 'inactive':
      return 'secondary'
    case 'archived':
      return 'warn'
    default:
      return 'info'
  }
}

function formatDateTime(dateString: string): string {
  return new Date(dateString).toLocaleString()
}

function isCurrentVersion(version: DocumentVersionSummary): boolean {
  return version.version === props.currentVersion
}

function viewVersion(version: DocumentVersionSummary) {
  emit('view-version', version.version)
}
</script>

<template>
  <div class="version-history">
    <div v-if="loading" class="loading-state">
      <i class="pi pi-spin pi-spinner"></i>
      <span>Loading version history...</span>
    </div>

    <div v-else-if="!versionHistory" class="empty-state">
      <i class="pi pi-history"></i>
      <p>No version history available</p>
    </div>

    <div v-else class="history-content">
      <div class="history-header">
        <div class="identity-hash">
          <span class="label">Identity Hash:</span>
          <code>{{ versionHistory.identity_hash }}</code>
        </div>
        <div class="version-count">
          {{ versionHistory.versions.length }} version(s)
        </div>
      </div>

      <Timeline :value="sortedVersions" class="version-timeline">
        <template #marker="slotProps">
          <div
            class="version-marker"
            :class="{ current: isCurrentVersion(slotProps.item) }"
          >
            <span>v{{ slotProps.item.version }}</span>
          </div>
        </template>

        <template #content="slotProps">
          <div class="version-item" :class="{ current: isCurrentVersion(slotProps.item) }">
            <div class="version-header">
              <Tag
                :value="slotProps.item.status"
                :severity="getStatusSeverity(slotProps.item.status)"
              />
              <span v-if="isCurrentVersion(slotProps.item)" class="current-badge">
                Current
              </span>
            </div>

            <div class="version-details">
              <div class="detail-row">
                <i class="pi pi-calendar"></i>
                <span>{{ formatDateTime(slotProps.item.created_at) }}</span>
              </div>
              <div v-if="slotProps.item.created_by" class="detail-row">
                <i class="pi pi-user"></i>
                <span>{{ slotProps.item.created_by }}</span>
              </div>
            </div>

            <div class="version-actions">
              <Button
                v-if="!isCurrentVersion(slotProps.item)"
                label="View"
                icon="pi pi-eye"
                severity="secondary"
                text
                size="small"
                @click="viewVersion(slotProps.item)"
              />
            </div>
          </div>
        </template>
      </Timeline>
    </div>
  </div>
</template>

<style scoped>
.version-history {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.loading-state,
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1rem;
  padding: 2rem;
  color: var(--p-text-muted-color);
}

.loading-state i,
.empty-state i {
  font-size: 2rem;
}

.history-content {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.history-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.75rem 1rem;
  background-color: var(--p-surface-50);
  border-radius: var(--p-border-radius);
}

.identity-hash {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.875rem;
}

.identity-hash .label {
  color: var(--p-text-muted-color);
}

.identity-hash code {
  background-color: var(--p-surface-100);
  padding: 0.25rem 0.5rem;
  border-radius: var(--p-border-radius);
  font-size: 0.75rem;
  word-break: break-all;
}

.version-count {
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
}

.version-timeline {
  padding: 0;
}

.version-marker {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 3rem;
  height: 1.75rem;
  background-color: var(--p-surface-200);
  border-radius: var(--p-border-radius);
  font-size: 0.75rem;
  font-weight: 500;
}

.version-marker.current {
  background-color: var(--p-primary-color);
  color: white;
}

.version-item {
  padding: 0.75rem 1rem;
  border: 1px solid var(--p-surface-200);
  border-radius: var(--p-border-radius);
  background-color: var(--p-surface-0);
}

.version-item.current {
  border-color: var(--p-primary-color);
  background-color: var(--p-primary-50);
}

.version-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}

.current-badge {
  font-size: 0.75rem;
  font-weight: 500;
  color: var(--p-primary-color);
  background-color: var(--p-primary-100);
  padding: 0.125rem 0.5rem;
  border-radius: var(--p-border-radius);
}

.version-details {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  margin-bottom: 0.5rem;
}

.detail-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.875rem;
  color: var(--p-text-muted-color);
}

.detail-row i {
  font-size: 0.75rem;
}

.version-actions {
  display: flex;
  gap: 0.5rem;
}
</style>
