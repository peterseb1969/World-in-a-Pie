<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Dropdown from 'primevue/dropdown'
import Textarea from 'primevue/textarea'
import Tag from 'primevue/tag'
import Card from 'primevue/card'
import { useNamespaceStore, useUiStore, useAuthStore } from '@/stores'
import type { NamespaceGroup, NamespaceGroupStats } from '@/stores/namespace'

const namespaceStore = useNamespaceStore()
const uiStore = useUiStore()
const authStore = useAuthStore()

// Dialog state
const showCreateDialog = ref(false)
const showStatsDialog = ref(false)
const selectedStats = ref<NamespaceGroupStats | null>(null)
const loadingStats = ref(false)

// Create form
const createForm = ref({
  prefix: '',
  description: '',
  isolation_mode: 'open' as 'open' | 'strict'
})

const isolationModeOptions = [
  { label: 'Open (allow cross-namespace refs)', value: 'open' },
  { label: 'Strict (same group only)', value: 'strict' }
]

// Load on mount
onMounted(() => {
  namespaceStore.loadGroups()
})

// Computed
const groups = computed(() => namespaceStore.groups)

function getSeverity(status: string): 'success' | 'warn' | 'danger' | 'secondary' {
  switch (status) {
    case 'active': return 'success'
    case 'archived': return 'warn'
    case 'deleted': return 'danger'
    default: return 'secondary'
  }
}

function getModeSeverity(mode: string): 'info' | 'warn' {
  return mode === 'strict' ? 'warn' : 'info'
}

// Actions
function openCreateDialog() {
  createForm.value = { prefix: '', description: '', isolation_mode: 'open' }
  showCreateDialog.value = true
}

async function createGroup() {
  if (!createForm.value.prefix.trim()) {
    uiStore.showError('Validation Error', 'Prefix is required')
    return
  }

  // Validate prefix format (alphanumeric and hyphens only)
  if (!/^[a-z0-9-]+$/.test(createForm.value.prefix)) {
    uiStore.showError('Validation Error', 'Prefix must contain only lowercase letters, numbers, and hyphens')
    return
  }

  try {
    await namespaceStore.createGroup({
      prefix: createForm.value.prefix,
      description: createForm.value.description,
      isolation_mode: createForm.value.isolation_mode,
      created_by: authStore.currentUser?.email || undefined
    })
    uiStore.showSuccess('Success', `Namespace group "${createForm.value.prefix}" created`)
    showCreateDialog.value = false
  } catch (e) {
    uiStore.showError('Error', e instanceof Error ? e.message : 'Failed to create group')
  }
}

async function viewStats(group: NamespaceGroup) {
  loadingStats.value = true
  selectedStats.value = null
  showStatsDialog.value = true
  try {
    selectedStats.value = await namespaceStore.getGroupStats(group.prefix)
  } catch (e) {
    uiStore.showError('Error', e instanceof Error ? e.message : 'Failed to load stats')
    showStatsDialog.value = false
  } finally {
    loadingStats.value = false
  }
}

async function archiveGroup(group: NamespaceGroup) {
  if (group.prefix === 'wip') {
    uiStore.showError('Error', 'Cannot archive the default wip namespace group')
    return
  }

  try {
    await namespaceStore.archiveGroup(group.prefix, authStore.currentUser?.email)
    uiStore.showSuccess('Success', `Namespace group "${group.prefix}" archived`)
  } catch (e) {
    uiStore.showError('Error', e instanceof Error ? e.message : 'Failed to archive group')
  }
}

async function restoreGroup(group: NamespaceGroup) {
  try {
    await namespaceStore.restoreGroup(group.prefix, authStore.currentUser?.email)
    uiStore.showSuccess('Success', `Namespace group "${group.prefix}" restored`)
  } catch (e) {
    uiStore.showError('Error', e instanceof Error ? e.message : 'Failed to restore group')
  }
}

function selectGroup(group: NamespaceGroup) {
  if (group.status !== 'active') {
    uiStore.showError('Error', 'Cannot select an archived or deleted namespace group')
    return
  }
  namespaceStore.setCurrentGroup(group.prefix)
  uiStore.showSuccess('Namespace Changed', `Now viewing "${group.prefix}" namespace`)
}

function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString()
}

// Total entity count for stats
function getTotalEntities(stats: NamespaceGroupStats): number {
  return Object.values(stats.namespaces).reduce((a, b) => a + b, 0)
}
</script>

<template>
  <div class="namespace-groups-view">
    <div class="page-header">
      <div>
        <h1>Namespace Groups</h1>
        <p class="subtitle">Manage namespace groups for data isolation and environment separation</p>
      </div>
      <Button
        label="Create Group"
        icon="pi pi-plus"
        @click="openCreateDialog"
      />
    </div>

    <Card>
      <template #content>
        <DataTable
          :value="groups"
          :loading="namespaceStore.loading"
          stripedRows
          class="groups-table"
        >
          <Column field="prefix" header="Prefix" sortable>
            <template #body="{ data }">
              <div class="prefix-cell">
                <strong>{{ data.prefix }}</strong>
                <Tag
                  v-if="data.prefix === namespaceStore.currentGroup"
                  value="CURRENT"
                  severity="success"
                  class="current-tag"
                />
              </div>
            </template>
          </Column>
          <Column field="description" header="Description" />
          <Column field="isolation_mode" header="Isolation" sortable>
            <template #body="{ data }">
              <Tag
                :value="data.isolation_mode.toUpperCase()"
                :severity="getModeSeverity(data.isolation_mode)"
              />
            </template>
          </Column>
          <Column field="status" header="Status" sortable>
            <template #body="{ data }">
              <Tag
                :value="data.status.toUpperCase()"
                :severity="getSeverity(data.status)"
              />
            </template>
          </Column>
          <Column field="created_at" header="Created" sortable>
            <template #body="{ data }">
              {{ formatDate(data.created_at) }}
            </template>
          </Column>
          <Column header="Actions" style="width: 200px">
            <template #body="{ data }">
              <div class="action-buttons">
                <Button
                  icon="pi pi-chart-bar"
                  text
                  rounded
                  size="small"
                  v-tooltip.top="'View Stats'"
                  @click="viewStats(data)"
                />
                <Button
                  v-if="data.status === 'active' && data.prefix !== namespaceStore.currentGroup"
                  icon="pi pi-check"
                  text
                  rounded
                  size="small"
                  severity="success"
                  v-tooltip.top="'Select'"
                  @click="selectGroup(data)"
                />
                <Button
                  v-if="data.status === 'active' && data.prefix !== 'wip'"
                  icon="pi pi-box"
                  text
                  rounded
                  size="small"
                  severity="warn"
                  v-tooltip.top="'Archive'"
                  @click="archiveGroup(data)"
                />
                <Button
                  v-if="data.status === 'archived'"
                  icon="pi pi-replay"
                  text
                  rounded
                  size="small"
                  severity="info"
                  v-tooltip.top="'Restore'"
                  @click="restoreGroup(data)"
                />
              </div>
            </template>
          </Column>
        </DataTable>
      </template>
    </Card>

    <!-- Create Dialog -->
    <Dialog
      v-model:visible="showCreateDialog"
      header="Create Namespace Group"
      :modal="true"
      :style="{ width: '500px' }"
    >
      <div class="create-form">
        <div class="field">
          <label for="prefix">Prefix *</label>
          <InputText
            id="prefix"
            v-model="createForm.prefix"
            placeholder="e.g., dev, staging, customer-abc"
            class="w-full"
          />
          <small class="help-text">
            Creates 5 namespaces: {prefix}-terminologies, -terms, -templates, -documents, -files
          </small>
        </div>

        <div class="field">
          <label for="description">Description</label>
          <Textarea
            id="description"
            v-model="createForm.description"
            rows="2"
            class="w-full"
            placeholder="Purpose of this namespace group"
          />
        </div>

        <div class="field">
          <label for="isolation">Isolation Mode</label>
          <Dropdown
            id="isolation"
            v-model="createForm.isolation_mode"
            :options="isolationModeOptions"
            optionLabel="label"
            optionValue="value"
            class="w-full"
          />
        </div>
      </div>

      <template #footer>
        <Button
          label="Cancel"
          severity="secondary"
          text
          @click="showCreateDialog = false"
        />
        <Button
          label="Create"
          @click="createGroup"
          :loading="namespaceStore.loading"
        />
      </template>
    </Dialog>

    <!-- Stats Dialog -->
    <Dialog
      v-model:visible="showStatsDialog"
      header="Namespace Group Statistics"
      :modal="true"
      :style="{ width: '500px' }"
    >
      <div v-if="loadingStats" class="loading-stats">
        <i class="pi pi-spin pi-spinner"></i>
        Loading statistics...
      </div>
      <div v-else-if="selectedStats" class="stats-content">
        <div class="stats-header">
          <h3>{{ selectedStats.prefix }}</h3>
          <Tag
            :value="selectedStats.status.toUpperCase()"
            :severity="getSeverity(selectedStats.status)"
          />
        </div>
        <p class="stats-description">{{ selectedStats.description }}</p>

        <div class="stats-summary">
          <div class="stat-total">
            <span class="stat-value">{{ getTotalEntities(selectedStats) }}</span>
            <span class="stat-label">Total Entities</span>
          </div>
        </div>

        <div class="namespace-stats">
          <div
            v-for="(count, ns) in selectedStats.namespaces"
            :key="ns"
            class="namespace-stat"
          >
            <span class="ns-name">{{ ns }}</span>
            <span class="ns-count">{{ count }}</span>
          </div>
        </div>
      </div>

      <template #footer>
        <Button
          label="Close"
          severity="secondary"
          text
          @click="showStatsDialog = false"
        />
      </template>
    </Dialog>
  </div>
</template>

<style scoped>
.namespace-groups-view {
  padding: 0;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 1.5rem;
}

.page-header h1 {
  margin: 0;
  font-size: 1.5rem;
  font-weight: 600;
}

.subtitle {
  margin: 0.25rem 0 0;
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
}

.prefix-cell {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.current-tag {
  font-size: 0.625rem;
}

.action-buttons {
  display: flex;
  gap: 0.25rem;
}

.create-form {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
}

.field label {
  font-weight: 500;
  font-size: 0.875rem;
}

.help-text {
  color: var(--p-text-muted-color);
  font-size: 0.75rem;
}

.w-full {
  width: 100%;
}

.loading-stats {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  padding: 2rem;
  color: var(--p-text-muted-color);
}

.stats-content {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.stats-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.stats-header h3 {
  margin: 0;
  font-size: 1.25rem;
}

.stats-description {
  margin: 0;
  color: var(--p-text-muted-color);
}

.stats-summary {
  display: flex;
  justify-content: center;
  padding: 1rem;
  background: var(--p-surface-50);
  border-radius: 8px;
}

.stat-total {
  display: flex;
  flex-direction: column;
  align-items: center;
}

.stat-value {
  font-size: 2rem;
  font-weight: 600;
  color: var(--p-primary-color);
}

.stat-label {
  font-size: 0.875rem;
  color: var(--p-text-muted-color);
}

.namespace-stats {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.namespace-stat {
  display: flex;
  justify-content: space-between;
  padding: 0.5rem 0.75rem;
  background: var(--p-surface-50);
  border-radius: 4px;
}

.ns-name {
  font-family: monospace;
  font-size: 0.875rem;
}

.ns-count {
  font-weight: 600;
}
</style>
