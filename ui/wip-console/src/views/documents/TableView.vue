<script setup lang="ts">
import { ref, onMounted, computed, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Button from 'primevue/button'
import Select from 'primevue/select'
import Tag from 'primevue/tag'
import Card from 'primevue/card'
import ProgressSpinner from 'primevue/progressspinner'
import Message from 'primevue/message'
import { useTemplateStore, useAuthStore, useUiStore } from '@/stores'
import { documentStoreClient } from '@/api/client'
import TruncatedId from '@/components/common/TruncatedId.vue'
import type { TableViewResponse, TableColumn, DocumentStatus } from '@/types'

const router = useRouter()
const route = useRoute()
const templateStore = useTemplateStore()
const authStore = useAuthStore()
const uiStore = useUiStore()

// State
const loading = ref(false)
const tableData = ref<TableViewResponse | null>(null)
const selectedTemplateId = ref<string | null>(null)
const statusFilter = ref<DocumentStatus>('active')
const pageSize = ref(100)
const currentPage = ref(1)

const statusOptions = [
  { label: 'Active', value: 'active' },
  { label: 'Inactive', value: 'inactive' },
  { label: 'Archived', value: 'archived' }
]

const pageSizeOptions = [
  { label: '25 rows', value: 25 },
  { label: '50 rows', value: 50 },
  { label: '100 rows', value: 100 },
  { label: '500 rows', value: 500 }
]

// Template options for dropdown
const templateOptions = computed(() => {
  return templateStore.templates
    .filter(t => t.status === 'active')
    .map(t => ({
      label: `${t.name} (${t.code})`,
      value: t.template_id
    }))
})

// Data columns from template
const dataColumns = computed(() => {
  if (!tableData.value) return []
  return tableData.value.columns
})

// Get column width based on type
function getColumnWidth(col: TableColumn): string {
  if (col.name === '_document_id') return '150px'
  if (col.name === '_identity_hash') return '150px'
  if (col.name === '_version') return '80px'
  if (col.name === '_status') return '100px'
  if (col.name === '_created_at' || col.name === '_updated_at') return '180px'
  if (col.type === 'boolean') return '80px'
  if (col.type === 'integer' || col.type === 'number') return '100px'
  if (col.type === 'date') return '120px'
  if (col.type === 'datetime') return '180px'
  return '150px'
}

// Format cell value based on type
function formatCellValue(value: unknown, col: TableColumn): string {
  if (value === null || value === undefined) return '-'

  if (col.type === 'boolean') {
    return value ? 'Yes' : 'No'
  }

  if (col.type === 'date' && typeof value === 'string') {
    return new Date(value).toLocaleDateString()
  }

  if (col.type === 'datetime' && typeof value === 'string') {
    return new Date(value).toLocaleString()
  }

  if (col.type === 'object' && typeof value === 'string') {
    // JSON objects are serialized as strings
    return value.length > 50 ? value.substring(0, 50) + '...' : value
  }

  return String(value)
}

// Format metadata value
function formatMetadataValue(value: unknown, colName: string): string {
  if (value === null || value === undefined) return '-'

  if (colName === '_created_at' || colName === '_updated_at') {
    return new Date(value as string).toLocaleString()
  }

  return String(value)
}

// Get status severity for tag
function getStatusSeverity(status: string): "success" | "warn" | "secondary" | "info" {
  switch (status) {
    case 'active': return 'info'
    case 'inactive': return 'warn'
    case 'archived': return 'secondary'
    default: return 'info'
  }
}

// Load table data
async function loadTableData() {
  if (!selectedTemplateId.value || !authStore.isAuthenticated) return

  loading.value = true
  try {
    tableData.value = await documentStoreClient.getTableView(selectedTemplateId.value, {
      status: statusFilter.value,
      page: currentPage.value,
      page_size: pageSize.value
    })
  } catch (e) {
    uiStore.showError('Failed to load table data', e instanceof Error ? e.message : 'Unknown error')
    tableData.value = null
  } finally {
    loading.value = false
  }
}

// Export to CSV
async function exportCsv() {
  if (!selectedTemplateId.value) return

  try {
    const blob = await documentStoreClient.exportTableCsv(selectedTemplateId.value, {
      status: statusFilter.value,
      include_metadata: true
    })

    // Create download link
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${tableData.value?.template_code || selectedTemplateId.value}.csv`
    document.body.appendChild(a)
    a.click()
    window.URL.revokeObjectURL(url)
    document.body.removeChild(a)

    uiStore.showSuccess('Export Complete', 'CSV file downloaded')
  } catch (e) {
    uiStore.showError('Export Failed', e instanceof Error ? e.message : 'Unknown error')
  }
}

// Navigate to document detail
function viewDocument(row: Record<string, unknown>) {
  const documentId = row._document_id as string
  if (documentId) {
    router.push(`/documents/${documentId}`)
  }
}

// Navigate to template detail
function viewTemplate() {
  if (selectedTemplateId.value) {
    router.push(`/templates/${selectedTemplateId.value}`)
  }
}

// Watch for filter changes
watch([statusFilter, pageSize], () => {
  currentPage.value = 1
  loadTableData()
})

watch(currentPage, loadTableData)

watch(selectedTemplateId, () => {
  currentPage.value = 1
  loadTableData()
})

// Load templates on mount
onMounted(async () => {
  if (!authStore.isAuthenticated) return

  await templateStore.fetchTemplates({ status: 'active', page_size: 100 })

  // Check if template_id is in query params
  const templateId = route.query.template as string
  if (templateId) {
    selectedTemplateId.value = templateId
  }
})
</script>

<template>
  <div class="table-view">
    <div class="page-header">
      <div class="header-left">
        <h1>Table View</h1>
        <span class="subtitle">View documents as a flattened table</span>
      </div>
    </div>

    <div v-if="!authStore.isAuthenticated" class="auth-warning">
      <i class="pi pi-exclamation-circle"></i>
      Please set your API key to access table view
    </div>

    <template v-else>
      <!-- Controls -->
      <Card class="controls-card">
        <template #content>
          <div class="controls">
            <div class="control-group">
              <label>Template</label>
              <Select
                v-model="selectedTemplateId"
                :options="templateOptions"
                optionLabel="label"
                optionValue="value"
                placeholder="Select a template"
                filter
                class="template-select"
              />
            </div>

            <div class="control-group">
              <label>Status</label>
              <Select
                v-model="statusFilter"
                :options="statusOptions"
                optionLabel="label"
                optionValue="value"
                class="status-select"
              />
            </div>

            <div class="control-group">
              <label>Page Size</label>
              <Select
                v-model="pageSize"
                :options="pageSizeOptions"
                optionLabel="label"
                optionValue="value"
                class="page-size-select"
              />
            </div>

            <div class="control-actions">
              <Button
                icon="pi pi-refresh"
                severity="secondary"
                @click="loadTableData"
                :disabled="!selectedTemplateId"
                v-tooltip="'Refresh'"
              />
              <Button
                icon="pi pi-download"
                label="Export CSV"
                severity="secondary"
                @click="exportCsv"
                :disabled="!selectedTemplateId || !tableData?.rows.length"
              />
              <Button
                icon="pi pi-external-link"
                label="View Template"
                severity="secondary"
                text
                @click="viewTemplate"
                :disabled="!selectedTemplateId"
              />
            </div>
          </div>
        </template>
      </Card>

      <!-- No template selected -->
      <Card v-if="!selectedTemplateId" class="empty-card">
        <template #content>
          <div class="empty-state">
            <i class="pi pi-table"></i>
            <p>Select a template to view documents as a table</p>
          </div>
        </template>
      </Card>

      <!-- Loading -->
      <Card v-else-if="loading" class="loading-card">
        <template #content>
          <div class="loading-state">
            <ProgressSpinner />
            <p>Loading table data...</p>
          </div>
        </template>
      </Card>

      <!-- Table data -->
      <Card v-else-if="tableData" class="table-card">
        <template #content>
          <!-- Table info -->
          <div class="table-info">
            <div class="info-left">
              <h2>{{ tableData.template_name }}</h2>
              <code>{{ tableData.template_code }}</code>
            </div>
            <div class="info-right">
              <span class="stat">
                <strong>{{ tableData.total_documents }}</strong> documents
              </span>
              <span class="stat" v-if="tableData.array_handling === 'flattened'">
                <i class="pi pi-arrow-right"></i>
                <strong>{{ tableData.total_rows }}</strong> rows (flattened)
              </span>
              <Tag
                v-if="tableData.array_handling !== 'none'"
                :value="tableData.array_handling === 'flattened' ? 'Arrays Flattened' : 'Arrays as JSON'"
                :severity="tableData.array_handling === 'flattened' ? 'info' : 'warn'"
              />
            </div>
          </div>

          <!-- Array flattening notice -->
          <Message v-if="tableData.array_handling === 'flattened'" severity="info" :closable="false" class="array-notice">
            Array fields have been flattened. Documents with multiple array values appear as multiple rows.
            Rows from the same document share the same <code>_document_id</code>.
          </Message>

          <!-- Data table -->
          <DataTable
            :value="tableData.rows"
            paginator
            :rows="pageSize"
            :rowsPerPageOptions="[25, 50, 100, 500]"
            stripedRows
            size="small"
            scrollable
            scrollHeight="600px"
            class="data-table"
            @row-click="(e) => viewDocument(e.data)"
            rowHover
          >
            <!-- Metadata columns -->
            <Column
              field="_document_id"
              header="Document ID"
              frozen
              style="min-width: 150px"
            >
              <template #body="{ data }">
                <a
                  class="document-link"
                  @click.stop="viewDocument(data)"
                >
                  <TruncatedId :id="data._document_id" :show-copy="false" />
                </a>
              </template>
            </Column>

            <Column field="_version" header="Ver" style="width: 60px">
              <template #body="{ data }">
                <span class="version">v{{ data._version }}</span>
              </template>
            </Column>

            <Column field="_status" header="Status" style="width: 100px">
              <template #body="{ data }">
                <Tag :value="data._status" :severity="getStatusSeverity(data._status)" />
              </template>
            </Column>

            <!-- Data columns -->
            <Column
              v-for="col in dataColumns"
              :key="col.name"
              :field="col.name"
              :header="col.label"
              :style="{ minWidth: getColumnWidth(col) }"
            >
              <template #body="{ data }">
                <span
                  :class="{
                    'array-value': col.is_array && col.is_flattened,
                    'json-value': col.type === 'object'
                  }"
                  :title="String(data[col.name] ?? '')"
                >
                  {{ formatCellValue(data[col.name], col) }}
                </span>
              </template>
            </Column>

            <!-- Timestamps -->
            <Column field="_created_at" header="Created" style="width: 180px">
              <template #body="{ data }">
                {{ formatMetadataValue(data._created_at, '_created_at') }}
              </template>
            </Column>

            <template #empty>
              <div class="empty-table">
                <i class="pi pi-inbox"></i>
                <p>No documents found for this template</p>
              </div>
            </template>
          </DataTable>

          <!-- Pagination info -->
          <div class="pagination-info">
            Page {{ tableData.page }} of {{ tableData.pages }}
            (showing {{ tableData.rows.length }} of {{ tableData.total_rows }} rows)
          </div>
        </template>
      </Card>
    </template>
  </div>
</template>

<style scoped>
.table-view {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.header-left h1 {
  margin: 0;
  font-size: 1.75rem;
}

.subtitle {
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
}

.auth-warning {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 1rem;
  background-color: var(--p-orange-50);
  border-radius: var(--p-border-radius);
  color: var(--p-orange-700);
}

.controls {
  display: flex;
  gap: 1.5rem;
  align-items: flex-end;
  flex-wrap: wrap;
}

.control-group {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
}

.control-group label {
  font-size: 0.75rem;
  font-weight: 500;
  color: var(--p-text-muted-color);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.template-select {
  min-width: 300px;
}

.status-select,
.page-size-select {
  min-width: 140px;
}

.control-actions {
  display: flex;
  gap: 0.5rem;
  margin-left: auto;
}

.empty-state,
.loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1rem;
  padding: 3rem;
  color: var(--p-text-muted-color);
}

.empty-state i,
.loading-state i {
  font-size: 3rem;
}

.table-info {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1rem;
  padding-bottom: 1rem;
  border-bottom: 1px solid var(--p-surface-200);
}

.info-left {
  display: flex;
  align-items: baseline;
  gap: 0.75rem;
}

.info-left h2 {
  margin: 0;
  font-size: 1.25rem;
}

.info-left code {
  background-color: var(--p-surface-100);
  padding: 0.25rem 0.5rem;
  border-radius: 4px;
  font-size: 0.75rem;
}

.info-right {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.stat {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
}

.stat strong {
  color: var(--p-text-color);
}

.array-notice {
  margin-bottom: 1rem;
}

.array-notice code {
  background-color: var(--p-surface-100);
  padding: 0.125rem 0.375rem;
  border-radius: 4px;
  font-size: 0.8125rem;
}

.data-table :deep(.p-datatable-tbody > tr) {
  cursor: pointer;
}

.document-link {
  color: var(--p-primary-color);
  text-decoration: none;
  cursor: pointer;
  font-family: monospace;
  font-size: 0.75rem;
}

.document-link:hover {
  text-decoration: underline;
}

.version {
  color: var(--p-text-muted-color);
  font-size: 0.75rem;
}

.array-value {
  background-color: var(--p-blue-50);
  padding: 0.125rem 0.375rem;
  border-radius: 4px;
}

.json-value {
  font-family: monospace;
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.empty-table {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1rem;
  padding: 3rem;
  color: var(--p-text-muted-color);
}

.empty-table i {
  font-size: 2rem;
}

.pagination-info {
  text-align: center;
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
  margin-top: 1rem;
}
</style>
