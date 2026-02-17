<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import DataTable, { type DataTablePageEvent } from 'primevue/datatable'
import Column from 'primevue/column'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Dropdown from 'primevue/dropdown'
import Tag from 'primevue/tag'
import Card from 'primevue/card'
import { useConfirm } from 'primevue/useconfirm'
import { registryClient } from '@/api/client'
import { useUiStore } from '@/stores'
import type { RegistryEntry } from '@/types'
import TruncatedId from '@/components/common/TruncatedId.vue'
import RegistrySearch from '@/components/registry/RegistrySearch.vue'
import NamespaceStatsStrip from '@/components/registry/NamespaceStatsStrip.vue'
import CompositeKeyDisplay from '@/components/registry/CompositeKeyDisplay.vue'

const router = useRouter()
const uiStore = useUiStore()
const confirm = useConfirm()

// Data
const entries = ref<RegistryEntry[]>([])
const total = ref(0)
const loading = ref(false)
const currentPage = ref(0)
const rowsPerPage = ref(25)
const selectedEntries = ref<RegistryEntry[]>([])

// Filters
const searchQuery = ref('')
const namespaceFilter = ref<string | null>(null)
const entityTypeFilter = ref<string | null>(null)
const statusFilter = ref<string | null>(null)

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

function navigateToDetail(entry: RegistryEntry) {
  router.push({ name: 'registry-detail', params: { id: entry.entry_id } })
}

function onNamespaceSelect(namespace: string | null) {
  namespaceFilter.value = namespace
}

function getStatusSeverity(status: string): 'success' | 'warn' | 'danger' | 'secondary' {
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

function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleString()
}

function clearFilters() {
  searchQuery.value = ''
  namespaceFilter.value = null
  entityTypeFilter.value = null
  statusFilter.value = null
}

function confirmBulkDeactivate() {
  if (selectedEntries.value.length === 0) return

  confirm.require({
    message: `Deactivate ${selectedEntries.value.length} selected entries?`,
    header: 'Confirm Deactivation',
    icon: 'pi pi-exclamation-triangle',
    acceptClass: 'p-button-danger',
    accept: executeBulkDeactivate,
  })
}

async function executeBulkDeactivate() {
  const ids = selectedEntries.value.map(e => e.entry_id)
  try {
    await registryClient.deactivateEntries(ids)
    uiStore.showSuccess(`Deactivated ${ids.length} entries`)
    selectedEntries.value = []
    await loadEntries()
  } catch (e) {
    uiStore.showError('Deactivation failed', (e as Error).message)
  }
}
</script>

<template>
  <div class="registry-list-view">
    <div class="page-header">
      <div>
        <h1>Registry</h1>
        <p class="subtitle">Identity management hub — browse, search, and manage registry entries</p>
      </div>
    </div>

    <!-- Unified Search -->
    <RegistrySearch class="search-section" />

    <!-- Namespace Stats -->
    <NamespaceStatsStrip @select="onNamespaceSelect" />

    <!-- Entry Table -->
    <Card>
      <template #content>
        <!-- Filters -->
        <div class="filters">
          <span class="p-input-icon-left filter-search">
            <i class="pi pi-search" />
            <InputText
              v-model="searchQuery"
              placeholder="Filter table by ID or key..."
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

        <!-- Bulk Actions -->
        <div v-if="selectedEntries.length > 0" class="bulk-actions">
          <span class="bulk-count">{{ selectedEntries.length }} selected</span>
          <Button
            :label="`Deactivate Selected (${selectedEntries.length})`"
            icon="pi pi-ban"
            severity="danger"
            size="small"
            outlined
            @click="confirmBulkDeactivate"
          />
        </div>

        <!-- Results Table -->
        <DataTable
          v-model:selection="selectedEntries"
          :value="entries"
          :loading="loading"
          :lazy="true"
          :paginator="true"
          :rows="rowsPerPage"
          :totalRecords="total"
          :rowsPerPageOptions="[25, 50, 100]"
          :first="currentPage * rowsPerPage"
          @page="onPage"
          @rowClick="(e: any) => navigateToDetail(e.data)"
          stripedRows
          size="small"
          dataKey="entry_id"
          class="registry-table clickable-rows"
        >
          <template #empty>
            <div class="empty-state">
              <i class="pi pi-inbox" style="font-size: 2rem; opacity: 0.3"></i>
              <p>No registry entries found</p>
            </div>
          </template>

          <Column selectionMode="multiple" style="width: 3rem" />

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

          <Column header="Composite Key" style="min-width: 200px">
            <template #body="{ data }">
              <CompositeKeyDisplay :compositeKey="data.primary_composite_key" compact />
            </template>
          </Column>

          <Column header="Syn" style="width: 50px" class="text-center">
            <template #body="{ data }">
              <span v-if="data.synonyms_count > 0" class="count-badge">{{ data.synonyms_count }}</span>
              <span v-else class="count-zero">-</span>
            </template>
          </Column>

          <Column field="status" header="Status" sortable style="width: 100px">
            <template #body="{ data }">
              <Tag :value="data.status" :severity="getStatusSeverity(data.status)" />
            </template>
          </Column>

          <Column field="created_at" header="Created" sortable style="width: 150px">
            <template #body="{ data }">
              <span class="date-text">{{ formatDate(data.created_at) }}</span>
            </template>
          </Column>
        </DataTable>
      </template>
    </Card>
  </div>
</template>

<style scoped>
.registry-list-view {
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

.search-section {
  margin-bottom: 1rem;
}

.filters {
  display: flex;
  gap: 0.75rem;
  align-items: center;
  margin-bottom: 1rem;
  flex-wrap: wrap;
}

.filter-search {
  position: relative;
  flex: 1;
  min-width: 200px;
}

.filter-search > i {
  position: absolute;
  left: 0.75rem;
  top: 50%;
  transform: translateY(-50%);
  color: var(--p-text-muted-color);
}

.filter-search > input {
  padding-left: 2.5rem;
  width: 100%;
}

.namespace-input {
  width: 140px;
}

.filter-dropdown {
  width: 150px;
}

.bulk-actions {
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 0.5rem 0.75rem;
  margin-bottom: 0.75rem;
  background: var(--p-surface-50);
  border-radius: 6px;
  border: 1px solid var(--p-surface-200);
}

.bulk-count {
  font-size: 0.8125rem;
  font-weight: 600;
  color: var(--p-primary-color);
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

.count-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 1.25rem;
  height: 1.25rem;
  padding: 0 0.25rem;
  font-size: 0.6875rem;
  font-weight: 600;
  background: var(--p-primary-100);
  color: var(--p-primary-700);
  border-radius: 10px;
}

.count-zero {
  color: var(--p-surface-300);
  font-size: 0.75rem;
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

:deep(.clickable-rows .p-datatable-tbody > tr) {
  cursor: pointer;
}

:deep(.clickable-rows .p-datatable-tbody > tr:hover) {
  background: var(--p-surface-50) !important;
}
</style>
