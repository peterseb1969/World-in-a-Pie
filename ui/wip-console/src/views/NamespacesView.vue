<script setup lang="ts">
import { ref, reactive, onMounted, computed } from 'vue'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import InputNumber from 'primevue/inputnumber'
import Dropdown from 'primevue/dropdown'
import Textarea from 'primevue/textarea'
import Tag from 'primevue/tag'
import Card from 'primevue/card'
import Panel from 'primevue/panel'
import { useNamespaceStore, useUiStore, useAuthStore } from '@/stores'
import type { Namespace, NamespaceStats } from '@/stores/namespace'
import type { IdAlgorithmConfig } from '@/api/client'

const namespaceStore = useNamespaceStore()
const uiStore = useUiStore()
const authStore = useAuthStore()

// Dialog state
const showCreateDialog = ref(false)
const showEditDialog = ref(false)
const showStatsDialog = ref(false)
const showIdConfigDialog = ref(false)
const selectedStats = ref<NamespaceStats | null>(null)
const loadingStats = ref(false)
const loadingIdConfig = ref(false)

// Inline stats per namespace
const inlineStats = ref<Record<string, Record<string, number>>>({})
const loadingInlineStats = ref(false)

// Entity types for ID configuration
const entityTypes = ['terminologies', 'terms', 'templates', 'documents', 'files']
const algorithmOptions = [
  { label: 'UUID7 (default)', value: 'uuid7' },
  { label: 'Prefixed Sequential', value: 'prefixed' },
  { label: 'NanoID', value: 'nanoid' }
]

interface IdConfigEntry {
  algorithm: 'uuid7' | 'prefixed' | 'nanoid'
  prefix: string
  pad: number
  length: number
}

function defaultIdConfigEntry(): IdConfigEntry {
  return { algorithm: 'uuid7', prefix: '', pad: 6, length: 21 }
}

// Create form
const createForm = ref({
  prefix: '',
  description: '',
  isolation_mode: 'open' as 'open' | 'strict'
})

const createIdConfig = reactive<Record<string, IdConfigEntry>>({})

// Edit form
const editForm = ref({
  prefix: '',
  description: '',
  isolation_mode: 'open' as 'open' | 'strict'
})

// ID Config edit form (separate dialog)
const idConfigForm = reactive<{ prefix: string; config: Record<string, IdConfigEntry> }>({
  prefix: '',
  config: {}
})

const isolationModeOptions = [
  { label: 'Open — Templates can reference terminologies from any namespace', value: 'open' },
  { label: 'Strict — Only this namespace\'s terminologies', value: 'strict' }
]

// Load on mount
onMounted(async () => {
  await namespaceStore.loadNamespaces()
  loadAllInlineStats()
})

async function loadAllInlineStats() {
  loadingInlineStats.value = true
  for (const ns of namespaceStore.namespaces) {
    try {
      const stats = await namespaceStore.getNamespaceStats(ns.prefix)
      inlineStats.value[ns.prefix] = stats.entity_counts
    } catch {
      // Skip if stats unavailable
    }
  }
  loadingInlineStats.value = false
}

function getInlineTotal(prefix: string): number {
  const counts = inlineStats.value[prefix]
  if (!counts) return 0
  return Object.values(counts).reduce((a, b) => a + b, 0)
}

// Computed
const namespaces = computed(() => namespaceStore.namespaces)

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
  for (const et of entityTypes) {
    createIdConfig[et] = defaultIdConfigEntry()
  }
  showCreateDialog.value = true
}

async function createNamespace() {
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
    // Build id_config from non-default entries
    const idConfig = buildIdConfigPayload(createIdConfig)
    await namespaceStore.createNamespace({
      prefix: createForm.value.prefix,
      description: createForm.value.description,
      isolation_mode: createForm.value.isolation_mode,
      id_config: Object.keys(idConfig).length > 0 ? idConfig : undefined,
      created_by: authStore.currentUser?.email || undefined
    })
    uiStore.showSuccess('Success', `Namespace "${createForm.value.prefix}" created`)
    showCreateDialog.value = false
    loadAllInlineStats()
  } catch (e) {
    uiStore.showError('Error', e instanceof Error ? e.message : 'Failed to create namespace')
  }
}

function openEditDialog(ns: Namespace) {
  editForm.value = {
    prefix: ns.prefix,
    description: ns.description,
    isolation_mode: ns.isolation_mode
  }
  showEditDialog.value = true
}

async function updateNamespace() {
  try {
    await namespaceStore.updateNamespace(editForm.value.prefix, {
      description: editForm.value.description,
      isolation_mode: editForm.value.isolation_mode,
      updated_by: authStore.currentUser?.email || undefined
    })
    uiStore.showSuccess('Success', `Namespace "${editForm.value.prefix}" updated`)
    showEditDialog.value = false
  } catch (e) {
    uiStore.showError('Error', e instanceof Error ? e.message : 'Failed to update namespace')
  }
}

async function viewStats(ns: Namespace) {
  loadingStats.value = true
  selectedStats.value = null
  showStatsDialog.value = true
  try {
    selectedStats.value = await namespaceStore.getNamespaceStats(ns.prefix)
  } catch (e) {
    uiStore.showError('Error', e instanceof Error ? e.message : 'Failed to load stats')
    showStatsDialog.value = false
  } finally {
    loadingStats.value = false
  }
}

async function archiveNamespace(ns: Namespace) {
  if (ns.prefix === 'wip') {
    uiStore.showError('Error', 'Cannot archive the default wip namespace')
    return
  }

  try {
    await namespaceStore.archiveNamespace(ns.prefix, authStore.currentUser?.email)
    uiStore.showSuccess('Success', `Namespace "${ns.prefix}" archived`)
  } catch (e) {
    uiStore.showError('Error', e instanceof Error ? e.message : 'Failed to archive namespace')
  }
}

async function restoreNamespace(ns: Namespace) {
  try {
    await namespaceStore.restoreNamespace(ns.prefix, authStore.currentUser?.email)
    uiStore.showSuccess('Success', `Namespace "${ns.prefix}" restored`)
  } catch (e) {
    uiStore.showError('Error', e instanceof Error ? e.message : 'Failed to restore namespace')
  }
}

function buildIdConfigPayload(config: Record<string, IdConfigEntry>): Record<string, IdAlgorithmConfig> {
  const result: Record<string, IdAlgorithmConfig> = {}
  for (const [et, entry] of Object.entries(config)) {
    if (entry.algorithm !== 'uuid7') {
      const cfg: IdAlgorithmConfig = { algorithm: entry.algorithm }
      if (entry.algorithm === 'prefixed') {
        cfg.prefix = entry.prefix
        cfg.pad = entry.pad
      } else if (entry.algorithm === 'nanoid') {
        cfg.length = entry.length
      }
      result[et] = cfg
    }
  }
  return result
}

function parseIdConfig(raw: Record<string, unknown>): Record<string, IdConfigEntry> {
  const result: Record<string, IdConfigEntry> = {}
  for (const et of entityTypes) {
    const cfg = raw[et] as Record<string, unknown> | undefined
    if (cfg && cfg.algorithm && cfg.algorithm !== 'uuid7') {
      result[et] = {
        algorithm: cfg.algorithm as IdConfigEntry['algorithm'],
        prefix: (cfg.prefix as string) || '',
        pad: (cfg.pad as number) || 6,
        length: (cfg.length as number) || 21
      }
    } else {
      result[et] = defaultIdConfigEntry()
    }
  }
  return result
}

async function openIdConfigDialog(ns: Namespace) {
  idConfigForm.prefix = ns.prefix
  loadingIdConfig.value = true
  showIdConfigDialog.value = true
  try {
    const config = await namespaceStore.getIdConfig(ns.prefix)
    idConfigForm.config = parseIdConfig(config)
  } catch {
    idConfigForm.config = Object.fromEntries(entityTypes.map(et => [et, defaultIdConfigEntry()]))
  } finally {
    loadingIdConfig.value = false
  }
}

async function saveIdConfig() {
  try {
    const idConfig = buildIdConfigPayload(idConfigForm.config)
    await namespaceStore.updateNamespace(idConfigForm.prefix, {
      id_config: idConfig,
      updated_by: authStore.currentUser?.email || undefined
    })
    uiStore.showSuccess('Success', `ID configuration for "${idConfigForm.prefix}" updated`)
    showIdConfigDialog.value = false
  } catch (e) {
    uiStore.showError('Error', e instanceof Error ? e.message : 'Failed to update ID config')
  }
}

function selectNamespace(ns: Namespace) {
  if (ns.status !== 'active') {
    uiStore.showError('Error', 'Cannot select an archived or deleted namespace')
    return
  }
  namespaceStore.setCurrent(ns.prefix)
  uiStore.showSuccess('Namespace Changed', `Now viewing "${ns.prefix}" namespace`)
}

function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString()
}

// Total entity count for stats
function getTotalEntities(stats: NamespaceStats): number {
  return Object.values(stats.entity_counts).reduce((a, b) => a + b, 0)
}
</script>

<template>
  <div class="namespaces-view">
    <div class="page-header">
      <div>
        <h1>Namespaces</h1>
        <p class="subtitle">Manage namespaces for data isolation and environment separation</p>
      </div>
      <Button
        v-if="namespaceStore.canCreateNamespace"
        label="Create Namespace"
        icon="pi pi-plus"
        @click="openCreateDialog"
      />
    </div>

    <Card>
      <template #content>
        <DataTable
          :value="namespaces"
          :loading="namespaceStore.loading"
          stripedRows
          class="namespaces-table"
        >
          <Column field="prefix" header="Prefix" sortable>
            <template #body="{ data }">
              <div class="prefix-cell">
                <strong>{{ data.prefix }}</strong>
                <Tag
                  v-if="data.prefix === namespaceStore.current"
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
          <Column header="Entities" style="width: 100px">
            <template #body="{ data }">
              <span v-if="inlineStats[data.prefix]" class="entity-count">
                {{ getInlineTotal(data.prefix) }}
              </span>
              <i v-else-if="loadingInlineStats" class="pi pi-spin pi-spinner" style="font-size: 0.75rem; color: var(--p-text-muted-color)"></i>
              <span v-else class="entity-count">-</span>
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
                  v-if="namespaceStore.isAdmin"
                  icon="pi pi-pencil"
                  text
                  rounded
                  size="small"
                  v-tooltip.top="'Edit'"
                  @click="openEditDialog(data)"
                />
                <Button
                  v-if="namespaceStore.isAdmin"
                  icon="pi pi-cog"
                  text
                  rounded
                  size="small"
                  v-tooltip.top="'ID Config'"
                  @click="openIdConfigDialog(data)"
                />
                <Button
                  icon="pi pi-chart-bar"
                  text
                  rounded
                  size="small"
                  v-tooltip.top="'View Stats'"
                  @click="viewStats(data)"
                />
                <Button
                  v-if="data.status === 'active' && data.prefix !== namespaceStore.current"
                  icon="pi pi-check"
                  text
                  rounded
                  size="small"
                  severity="success"
                  v-tooltip.top="'Select'"
                  @click="selectNamespace(data)"
                />
                <Button
                  v-if="namespaceStore.isAdmin && data.status === 'active' && data.prefix !== 'wip'"
                  icon="pi pi-box"
                  text
                  rounded
                  size="small"
                  severity="warn"
                  v-tooltip.top="'Archive'"
                  @click="archiveNamespace(data)"
                />
                <Button
                  v-if="namespaceStore.isAdmin && data.status === 'archived'"
                  icon="pi pi-replay"
                  text
                  rounded
                  size="small"
                  severity="info"
                  v-tooltip.top="'Restore'"
                  @click="restoreNamespace(data)"
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
      header="Create Namespace"
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
            Namespace prefix used to isolate terminologies, terms, templates, documents, and files
          </small>
        </div>

        <div class="field">
          <label for="description">Description</label>
          <Textarea
            id="description"
            v-model="createForm.description"
            rows="2"
            class="w-full"
            placeholder="Purpose of this namespace"
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

        <Panel header="ID Configuration" toggleable :collapsed="true" class="id-config-panel">
          <small class="help-text id-config-help">Configure how IDs are generated per entity type. Default is UUID7 for all types.</small>
          <div v-for="et in entityTypes" :key="et" class="id-config-row">
            <label class="id-config-label">{{ et }}</label>
            <Dropdown
              v-model="createIdConfig[et].algorithm"
              :options="algorithmOptions"
              optionLabel="label"
              optionValue="value"
              class="id-config-algo"
            />
            <template v-if="createIdConfig[et]?.algorithm === 'prefixed'">
              <InputText
                v-model="createIdConfig[et].prefix"
                placeholder="Prefix"
                class="id-config-prefix"
              />
              <InputNumber
                v-model="createIdConfig[et].pad"
                :min="1"
                :max="12"
                placeholder="Pad"
                class="id-config-num"
              />
            </template>
            <template v-if="createIdConfig[et]?.algorithm === 'nanoid'">
              <InputNumber
                v-model="createIdConfig[et].length"
                :min="6"
                :max="36"
                placeholder="Length"
                class="id-config-num"
              />
            </template>
          </div>
        </Panel>
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
          @click="createNamespace"
          :loading="namespaceStore.loading"
        />
      </template>
    </Dialog>

    <!-- Edit Dialog -->
    <Dialog
      v-model:visible="showEditDialog"
      header="Edit Namespace"
      :modal="true"
      :style="{ width: '500px' }"
    >
      <div class="create-form">
        <div class="field">
          <label>Prefix</label>
          <InputText
            :model-value="editForm.prefix"
            disabled
            class="w-full"
          />
        </div>

        <div class="field">
          <label for="edit-description">Description</label>
          <Textarea
            id="edit-description"
            v-model="editForm.description"
            rows="2"
            class="w-full"
          />
        </div>

        <div class="field">
          <label for="edit-isolation">Isolation Mode</label>
          <Dropdown
            id="edit-isolation"
            v-model="editForm.isolation_mode"
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
          @click="showEditDialog = false"
        />
        <Button
          label="Save"
          @click="updateNamespace"
          :loading="namespaceStore.loading"
        />
      </template>
    </Dialog>

    <!-- Stats Dialog -->
    <Dialog
      v-model:visible="showStatsDialog"
      header="Namespace Statistics"
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

        <div class="pool-stats">
          <div
            v-for="(count, pool) in selectedStats.entity_counts"
            :key="pool"
            class="pool-stat"
          >
            <span class="pool-name">{{ pool }}</span>
            <span class="pool-count">{{ count }}</span>
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
    <!-- ID Config Dialog -->
    <Dialog
      v-model:visible="showIdConfigDialog"
      :header="`ID Configuration — ${idConfigForm.prefix}`"
      :modal="true"
      :style="{ width: '600px' }"
    >
      <div v-if="loadingIdConfig" class="loading-stats">
        <i class="pi pi-spin pi-spinner"></i>
        Loading configuration...
      </div>
      <div v-else class="id-config-content">
        <small class="help-text id-config-help">
          Changes only affect newly generated IDs. Existing entries keep their current IDs.
        </small>
        <div v-for="et in entityTypes" :key="et" class="id-config-row">
          <label class="id-config-label">{{ et }}</label>
          <Dropdown
            v-model="idConfigForm.config[et].algorithm"
            :options="algorithmOptions"
            optionLabel="label"
            optionValue="value"
            class="id-config-algo"
          />
          <template v-if="idConfigForm.config[et]?.algorithm === 'prefixed'">
            <InputText
              v-model="idConfigForm.config[et].prefix"
              placeholder="Prefix (e.g. TPL-)"
              class="id-config-prefix"
            />
            <InputNumber
              v-model="idConfigForm.config[et].pad"
              :min="1"
              :max="12"
              placeholder="Pad"
              class="id-config-num"
            />
          </template>
          <template v-if="idConfigForm.config[et]?.algorithm === 'nanoid'">
            <InputNumber
              v-model="idConfigForm.config[et].length"
              :min="6"
              :max="36"
              placeholder="Length"
              class="id-config-num"
            />
          </template>
        </div>
      </div>

      <template #footer>
        <Button
          label="Cancel"
          severity="secondary"
          text
          @click="showIdConfigDialog = false"
        />
        <Button
          label="Save"
          @click="saveIdConfig"
          :loading="namespaceStore.loading"
        />
      </template>
    </Dialog>
  </div>
</template>

<style scoped>
.namespaces-view {
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

.entity-count {
  font-weight: 600;
  color: var(--p-primary-color);
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

.pool-stats {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.pool-stat {
  display: flex;
  justify-content: space-between;
  padding: 0.5rem 0.75rem;
  background: var(--p-surface-50);
  border-radius: 4px;
}

.pool-name {
  font-family: monospace;
  font-size: 0.875rem;
}

.pool-count {
  font-weight: 600;
}

.id-config-panel {
  margin-top: 0.5rem;
}

.id-config-help {
  display: block;
  margin-bottom: 0.75rem;
}

.id-config-content {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.id-config-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
}

.id-config-label {
  width: 110px;
  font-family: monospace;
  font-size: 0.875rem;
  font-weight: 500;
  flex-shrink: 0;
}

.id-config-algo {
  width: 180px;
  flex-shrink: 0;
}

.id-config-prefix {
  width: 120px;
}

.id-config-num {
  width: 80px;
}
</style>
