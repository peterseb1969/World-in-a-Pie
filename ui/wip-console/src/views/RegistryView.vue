<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import DataTable, { type DataTablePageEvent } from 'primevue/datatable'
import Column from 'primevue/column'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Dropdown from 'primevue/dropdown'
import Tag from 'primevue/tag'
import Card from 'primevue/card'
import Dialog from 'primevue/dialog'
import { registryClient } from '@/api/client'
import { useUiStore } from '@/stores'
import type { RegistryEntry, RegistryLookupResponse } from '@/types'
import TruncatedId from '@/components/common/TruncatedId.vue'

const uiStore = useUiStore()

// Data
const entries = ref<RegistryEntry[]>([])
const total = ref(0)
const loading = ref(false)
const currentPage = ref(0)
const rowsPerPage = ref(25)

// Filters
const searchQuery = ref('')
const namespaceFilter = ref<string | null>(null)
const entityTypeFilter = ref<string | null>(null)
const statusFilter = ref<string | null>(null)

// Detail dialog
const showDetailDialog = ref(false)
const detailLoading = ref(false)
const selectedEntry = ref<RegistryEntry | null>(null)
const entryDetail = ref<RegistryLookupResponse | null>(null)

const entityTypeOptions = [
  { label: 'All Types', value: null },
  { label: 'Terminologies', value: 'terminologies' },
  { label: 'Terms', value: 'terms' },
  { label: 'Templates', value: 'templates' },
  { label: 'Documents', value: 'documents' },
  { label: 'Files', value: 'files' }
]

const statusOptions = [
  { label: 'All Statuses', value: null },
  { label: 'Active', value: 'active' },
  { label: 'Reserved', value: 'reserved' },
  { label: 'Inactive', value: 'inactive' }
]

// Debounced search
let searchTimeout: ReturnType<typeof setTimeout> | null = null

onMounted(() => {
  loadEntries()
})

watch(searchQuery, () => {
  if (searchTimeout) clearTimeout(searchTimeout)
  searchTimeout = setTimeout(() => {
    currentPage.value = 0
    loadEntries()
  }, 300)
})

watch([namespaceFilter, entityTypeFilter, statusFilter], () => {
  currentPage.value = 0
  loadEntries()
})

async function loadEntries() {
  loading.value = true
  try {
    const response = await registryClient.listEntries({
      page: currentPage.value + 1,
      page_size: rowsPerPage.value,
      namespace: namespaceFilter.value || undefined,
      entity_type: entityTypeFilter.value || undefined,
      status: statusFilter.value || undefined,
      q: searchQuery.value || undefined
    })
    entries.value = response.items
    total.value = response.total
  } catch (e) {
    uiStore.showError('Failed to load entries', (e as Error).message)
  } finally {
    loading.value = false
  }
}

function onPage(event: DataTablePageEvent) {
  currentPage.value = event.page
  rowsPerPage.value = event.rows
  loadEntries()
}

async function viewDetail(entry: RegistryEntry) {
  selectedEntry.value = entry
  entryDetail.value = null
  showDetailDialog.value = true
  detailLoading.value = true
  try {
    entryDetail.value = await registryClient.lookupEntry(entry.entry_id)
  } catch (e) {
    uiStore.showError('Failed to load entry details', (e as Error).message)
  } finally {
    detailLoading.value = false
  }
}

function getStatusSeverity(status: string): 'success' | 'warn' | 'danger' | 'info' | 'secondary' {
  switch (status) {
    case 'active': return 'success'
    case 'reserved': return 'warn'
    case 'inactive': return 'danger'
    default: return 'secondary'
  }
}

function getEntityTypeIcon(type: string): string {
  switch (type) {
    case 'terminologies': return 'pi pi-book'
    case 'terms': return 'pi pi-tag'
    case 'templates': return 'pi pi-file'
    case 'documents': return 'pi pi-folder'
    case 'files': return 'pi pi-images'
    default: return 'pi pi-circle'
  }
}

function formatCompositeKey(key: Record<string, unknown>): string {
  if (!key || Object.keys(key).length === 0) return '(empty)'
  return Object.entries(key)
    .map(([k, v]) => `${k}: ${v}`)
    .join(', ')
}

function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleString()
}

function clearFilters() {
  searchQuery.value = ''
  namespaceFilter.value = null
  entityTypeFilter.value = null
  statusFilter.value = null
}
</script>

<template>
  <div class="registry-view">
    <div class="page-header">
      <div>
        <h1>Registry</h1>
        <p class="subtitle">Browse and search registry entries across all namespaces</p>
      </div>
    </div>

    <Card>
      <template #content>
        <!-- Filters -->
        <div class="filters">
          <span class="p-input-icon-left search-input">
            <i class="pi pi-search" />
            <InputText
              v-model="searchQuery"
              placeholder="Search by ID or composite key..."
            />
          </span>
          <InputText
            v-model="namespaceFilter"
            placeholder="Namespace..."
            class="namespace-input"
          />
          <Dropdown
            v-model="entityTypeFilter"
            :options="entityTypeOptions"
            optionLabel="label"
            optionValue="value"
            placeholder="Entity Type"
            class="filter-dropdown"
          />
          <Dropdown
            v-model="statusFilter"
            :options="statusOptions"
            optionLabel="label"
            optionValue="value"
            placeholder="Status"
            class="filter-dropdown"
          />
          <Button
            icon="pi pi-filter-slash"
            text
            rounded
            v-tooltip.top="'Clear filters'"
            @click="clearFilters"
          />
        </div>

        <!-- Results Table -->
        <DataTable
          :value="entries"
          :loading="loading"
          :lazy="true"
          :paginator="true"
          :rows="rowsPerPage"
          :totalRecords="total"
          :rowsPerPageOptions="[25, 50, 100]"
          :first="currentPage * rowsPerPage"
          @page="onPage"
          stripedRows
          size="small"
          dataKey="entry_id"
          class="registry-table"
        >
          <template #empty>
            <div class="empty-state">
              <i class="pi pi-inbox" style="font-size: 2rem; opacity: 0.3"></i>
              <p>No registry entries found</p>
            </div>
          </template>

          <Column field="entry_id" header="Entry ID" sortable style="width: 180px">
            <template #body="{ data }">
              <TruncatedId :id="data.entry_id" :length="16" />
            </template>
          </Column>

          <Column field="namespace" header="Namespace" sortable style="width: 120px">
            <template #body="{ data }">
              <code class="namespace-badge">{{ data.namespace }}</code>
            </template>
          </Column>

          <Column field="entity_type" header="Entity Type" sortable style="width: 140px">
            <template #body="{ data }">
              <span class="entity-type">
                <i :class="getEntityTypeIcon(data.entity_type)"></i>
                {{ data.entity_type }}
              </span>
            </template>
          </Column>

          <Column field="primary_composite_key" header="Composite Key" style="width: 30%">
            <template #body="{ data }">
              <code class="composite-key">{{ formatCompositeKey(data.primary_composite_key) }}</code>
            </template>
          </Column>

          <Column field="status" header="Status" sortable style="width: 100px">
            <template #body="{ data }">
              <Tag
                :value="data.status"
                :severity="getStatusSeverity(data.status)"
              />
            </template>
          </Column>

          <Column field="created_at" header="Created" sortable style="width: 160px">
            <template #body="{ data }">
              <span class="date-text">{{ formatDate(data.created_at) }}</span>
            </template>
          </Column>

          <Column header="" style="width: 60px">
            <template #body="{ data }">
              <Button
                icon="pi pi-eye"
                text
                rounded
                size="small"
                v-tooltip.top="'View Details'"
                @click="viewDetail(data)"
              />
            </template>
          </Column>
        </DataTable>
      </template>
    </Card>

    <!-- Detail Dialog -->
    <Dialog
      v-model:visible="showDetailDialog"
      :header="`Entry: ${selectedEntry?.entry_id ?? ''}`"
      :modal="true"
      :style="{ width: '650px' }"
    >
      <div v-if="detailLoading" class="loading-state">
        <i class="pi pi-spin pi-spinner"></i>
        Loading details...
      </div>
      <div v-else-if="entryDetail" class="detail-content">
        <div class="detail-section">
          <h4>General</h4>
          <div class="detail-grid">
            <span class="detail-label">Entry ID</span>
            <code>{{ entryDetail.preferred_id }}</code>
            <span class="detail-label">Namespace</span>
            <span>{{ entryDetail.namespace }}</span>
            <span class="detail-label">Entity Type</span>
            <span>{{ entryDetail.entity_type }}</span>
            <span class="detail-label">Matched Via</span>
            <span>{{ entryDetail.matched_via || 'N/A' }}</span>
          </div>
        </div>

        <div class="detail-section" v-if="entryDetail.matched_composite_key">
          <h4>Primary Composite Key</h4>
          <div class="key-pairs">
            <div v-for="(value, key) in entryDetail.matched_composite_key" :key="String(key)" class="key-pair">
              <span class="key-name">{{ key }}</span>
              <code class="key-value">{{ value }}</code>
            </div>
            <div v-if="Object.keys(entryDetail.matched_composite_key).length === 0" class="empty-key">
              (empty — no deduplication key)
            </div>
          </div>
        </div>

        <div class="detail-section" v-if="entryDetail.additional_ids?.length">
          <h4>Additional IDs ({{ entryDetail.additional_ids.length }})</h4>
          <div class="additional-ids">
            <code v-for="(aid, idx) in entryDetail.additional_ids" :key="idx" class="additional-id">
              {{ aid.id || JSON.stringify(aid) }}
            </code>
          </div>
        </div>

        <div class="detail-section" v-if="entryDetail.synonyms?.length">
          <h4>Synonyms ({{ entryDetail.synonyms.length }})</h4>
          <div v-for="(syn, idx) in entryDetail.synonyms" :key="idx" class="synonym-item">
            <div class="synonym-header">
              <Tag :value="syn.entity_type" severity="info" />
              <code>{{ syn.namespace }}</code>
            </div>
            <div class="key-pairs">
              <div v-for="(value, key) in syn.composite_key" :key="String(key)" class="key-pair">
                <span class="key-name">{{ key }}</span>
                <code class="key-value">{{ value }}</code>
              </div>
            </div>
          </div>
        </div>

        <div class="detail-section" v-if="entryDetail.source_info">
          <h4>Source Info</h4>
          <div class="detail-grid">
            <span class="detail-label">System ID</span>
            <span>{{ entryDetail.source_info.system_id }}</span>
            <span class="detail-label">Endpoint</span>
            <code v-if="entryDetail.source_info.endpoint_url">{{ entryDetail.source_info.endpoint_url }}</code>
            <span v-else>N/A</span>
          </div>
        </div>

        <div class="detail-section" v-if="selectedEntry">
          <h4>Timestamps</h4>
          <div class="detail-grid">
            <span class="detail-label">Created</span>
            <span>{{ formatDate(selectedEntry.created_at) }}</span>
            <span class="detail-label">Created By</span>
            <span>{{ selectedEntry.created_by || 'N/A' }}</span>
            <span class="detail-label">Updated</span>
            <span>{{ formatDate(selectedEntry.updated_at) }}</span>
          </div>
        </div>
      </div>
      <div v-else class="empty-detail">
        <p>Entry details not available</p>
      </div>

      <template #footer>
        <Button
          label="Close"
          severity="secondary"
          text
          @click="showDetailDialog = false"
        />
      </template>
    </Dialog>
  </div>
</template>

<style scoped>
.registry-view {
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

.filters {
  display: flex;
  gap: 0.75rem;
  align-items: center;
  margin-bottom: 1rem;
  flex-wrap: wrap;
}

.search-input {
  position: relative;
  flex: 1;
  min-width: 200px;
}

.search-input > i {
  position: absolute;
  left: 0.75rem;
  top: 50%;
  transform: translateY(-50%);
  color: var(--p-text-muted-color);
}

.search-input > input {
  padding-left: 2.5rem;
  width: 100%;
}

.namespace-input {
  width: 140px;
}

.filter-dropdown {
  width: 150px;
}

.namespace-badge {
  font-size: 0.8rem;
  background: var(--p-surface-100);
  padding: 0.125rem 0.375rem;
  border-radius: 3px;
}

.entity-type {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.875rem;
}

.entity-type i {
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
}

.composite-key {
  font-size: 0.8rem;
  background: var(--p-surface-50);
  padding: 0.125rem 0.375rem;
  border-radius: 3px;
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.date-text {
  font-size: 0.8rem;
  color: var(--p-text-muted-color);
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.5rem;
  padding: 2rem;
  color: var(--p-text-muted-color);
}

/* Detail Dialog */
.loading-state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  padding: 2rem;
  color: var(--p-text-muted-color);
}

.detail-content {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}

.detail-section h4 {
  margin: 0 0 0.5rem;
  font-size: 0.875rem;
  font-weight: 600;
  color: var(--p-text-muted-color);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.detail-grid {
  display: grid;
  grid-template-columns: 120px 1fr;
  gap: 0.375rem 1rem;
  font-size: 0.875rem;
}

.detail-label {
  color: var(--p-text-muted-color);
  font-weight: 500;
}

.key-pairs {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.key-pair {
  display: flex;
  gap: 0.5rem;
  align-items: center;
  padding: 0.25rem 0.5rem;
  background: var(--p-surface-50);
  border-radius: 4px;
  font-size: 0.875rem;
}

.key-name {
  color: var(--p-text-muted-color);
  font-weight: 500;
  min-width: 80px;
}

.key-value {
  font-family: monospace;
  font-size: 0.8rem;
}

.empty-key {
  color: var(--p-text-muted-color);
  font-style: italic;
  font-size: 0.875rem;
}

.additional-ids {
  display: flex;
  flex-wrap: wrap;
  gap: 0.375rem;
}

.additional-id {
  font-size: 0.8rem;
  background: var(--p-surface-100);
  padding: 0.25rem 0.5rem;
  border-radius: 4px;
}

.synonym-item {
  padding: 0.5rem;
  background: var(--p-surface-50);
  border-radius: 6px;
  margin-bottom: 0.5rem;
}

.synonym-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.375rem;
}

.empty-detail {
  text-align: center;
  padding: 2rem;
  color: var(--p-text-muted-color);
}
</style>
