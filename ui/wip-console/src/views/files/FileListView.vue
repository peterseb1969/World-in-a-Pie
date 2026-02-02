<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useConfirm } from 'primevue/useconfirm'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'
import Tag from 'primevue/tag'
import Message from 'primevue/message'
import ProgressSpinner from 'primevue/progressspinner'
import { fileStoreClient } from '@/api/client'
import { useUiStore } from '@/stores'
import type { FileEntity, FileStatus, FileListResponse } from '@/types'

const router = useRouter()
const confirm = useConfirm()
const uiStore = useUiStore()

// State
const files = ref<FileEntity[]>([])
const loading = ref(false)
const storageEnabled = ref<boolean | null>(null)
const totalRecords = ref(0)
const currentPage = ref(1)
const pageSize = ref(20)

// Filters
const statusFilter = ref<FileStatus | null>(null)
const contentTypeFilter = ref('')
const categoryFilter = ref('')

const statusOptions = [
  { label: 'All', value: null },
  { label: 'Active', value: 'active' },
  { label: 'Orphan', value: 'orphan' },
  { label: 'Inactive', value: 'inactive' }
]

// Check storage status
async function checkStorageEnabled() {
  try {
    storageEnabled.value = await fileStoreClient.isStorageEnabled()
  } catch {
    storageEnabled.value = false
  }
}

// Load files
async function loadFiles() {
  if (!storageEnabled.value) return

  loading.value = true
  try {
    const response: FileListResponse = await fileStoreClient.listFiles({
      status: statusFilter.value || undefined,
      content_type: contentTypeFilter.value || undefined,
      category: categoryFilter.value || undefined,
      page: currentPage.value,
      page_size: pageSize.value
    })
    files.value = response.items
    totalRecords.value = response.total
  } catch (e) {
    uiStore.showError('Error', (e as Error).message)
  } finally {
    loading.value = false
  }
}

// Pagination
function onPage(event: { page: number; rows: number }) {
  currentPage.value = event.page + 1
  pageSize.value = event.rows
  loadFiles()
}

// View file details
function viewFile(file: FileEntity) {
  router.push(`/files/${file.file_id}`)
}

// Download file
async function downloadFile(file: FileEntity) {
  try {
    const response = await fileStoreClient.getDownloadUrl(file.file_id)
    window.open(response.download_url, '_blank')
  } catch (e) {
    uiStore.showError('Download Failed', (e as Error).message)
  }
}

// Delete file
function confirmDelete(file: FileEntity) {
  confirm.require({
    message: `Are you sure you want to delete "${file.filename}"?`,
    header: 'Delete File',
    icon: 'pi pi-exclamation-triangle',
    acceptClass: 'p-button-danger',
    accept: async () => {
      try {
        await fileStoreClient.deleteFile(file.file_id)
        uiStore.showSuccess('Deleted', `File "${file.filename}" has been deleted`)
        loadFiles()
      } catch (e) {
        uiStore.showError('Delete Failed', (e as Error).message)
      }
    }
  })
}

// Format file size
function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

// Get file icon based on content type
function getFileIcon(contentType: string): string {
  if (contentType.startsWith('image/')) return 'pi-image'
  if (contentType.startsWith('video/')) return 'pi-video'
  if (contentType.startsWith('audio/')) return 'pi-volume-up'
  if (contentType === 'application/pdf') return 'pi-file-pdf'
  if (contentType.includes('word') || contentType.includes('document')) return 'pi-file-word'
  if (contentType.includes('excel') || contentType.includes('spreadsheet')) return 'pi-file-excel'
  return 'pi-file'
}

// Get status severity
function getStatusSeverity(status: string): 'success' | 'warn' | 'danger' | 'secondary' | undefined {
  switch (status) {
    case 'active': return 'success'
    case 'orphan': return 'warn'
    case 'inactive': return 'danger'
    default: return 'secondary'
  }
}

// Format date
function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleString()
}

// Watch filters
watch([statusFilter, contentTypeFilter, categoryFilter], () => {
  currentPage.value = 1
  loadFiles()
})

onMounted(async () => {
  await checkStorageEnabled()
  if (storageEnabled.value) {
    loadFiles()
  }
})
</script>

<template>
  <div class="file-list-view">
    <div class="page-header">
      <h1>Files</h1>
      <Button
        label="Upload"
        icon="pi pi-upload"
        @click="router.push('/files/upload')"
        :disabled="!storageEnabled"
      />
    </div>

    <!-- Storage not enabled warning -->
    <Message v-if="storageEnabled === false" severity="warn" :closable="false" class="storage-warning">
      <div class="warning-content">
        <i class="pi pi-exclamation-triangle"></i>
        <div>
          <strong>File storage is not enabled.</strong>
          <p>Set <code>WIP_FILE_STORAGE_ENABLED=true</code> and configure MinIO to enable file storage.</p>
        </div>
      </div>
    </Message>

    <!-- Loading storage check -->
    <div v-else-if="storageEnabled === null" class="loading-state">
      <ProgressSpinner />
      <span>Checking storage status...</span>
    </div>

    <!-- Main content -->
    <template v-else>
      <!-- Filters -->
      <div class="filters">
        <div class="filter-group">
          <label>Status</label>
          <Select
            v-model="statusFilter"
            :options="statusOptions"
            optionLabel="label"
            optionValue="value"
            placeholder="All"
            class="filter-select"
          />
        </div>
        <div class="filter-group">
          <label>Content Type</label>
          <InputText
            v-model="contentTypeFilter"
            placeholder="e.g., image/*"
            class="filter-input"
          />
        </div>
        <div class="filter-group">
          <label>Category</label>
          <InputText
            v-model="categoryFilter"
            placeholder="Filter by category"
            class="filter-input"
          />
        </div>
      </div>

      <!-- Data Table -->
      <DataTable
        :value="files"
        :loading="loading"
        :paginator="true"
        :rows="pageSize"
        :totalRecords="totalRecords"
        :lazy="true"
        @page="onPage"
        :rowsPerPageOptions="[10, 20, 50]"
        stripedRows
        showGridlines
        dataKey="file_id"
        class="files-table"
      >
        <template #empty>
          <div class="empty-state">
            <i class="pi pi-images"></i>
            <span>No files found</span>
          </div>
        </template>

        <Column header="" style="width: 3rem">
          <template #body="{ data }">
            <i :class="['pi', getFileIcon(data.content_type), 'file-icon']"></i>
          </template>
        </Column>

        <Column field="filename" header="Filename" sortable style="min-width: 200px">
          <template #body="{ data }">
            <a class="file-link" @click="viewFile(data)">{{ data.filename }}</a>
          </template>
        </Column>

        <Column field="file_id" header="ID" style="width: 130px">
          <template #body="{ data }">
            <code class="file-id">{{ data.file_id }}</code>
          </template>
        </Column>

        <Column field="content_type" header="Type" style="width: 150px">
          <template #body="{ data }">
            <span class="content-type">{{ data.content_type }}</span>
          </template>
        </Column>

        <Column field="size_bytes" header="Size" style="width: 100px">
          <template #body="{ data }">
            {{ formatFileSize(data.size_bytes) }}
          </template>
        </Column>

        <Column field="status" header="Status" style="width: 100px">
          <template #body="{ data }">
            <Tag :value="data.status" :severity="getStatusSeverity(data.status)" />
          </template>
        </Column>

        <Column field="reference_count" header="Refs" style="width: 80px">
          <template #body="{ data }">
            <span :class="{ 'no-refs': data.reference_count === 0 }">
              {{ data.reference_count }}
            </span>
          </template>
        </Column>

        <Column field="uploaded_at" header="Uploaded" style="width: 160px">
          <template #body="{ data }">
            {{ formatDate(data.uploaded_at) }}
          </template>
        </Column>

        <Column header="Actions" style="width: 120px">
          <template #body="{ data }">
            <div class="actions">
              <Button
                icon="pi pi-download"
                severity="secondary"
                text
                rounded
                size="small"
                @click="downloadFile(data)"
                v-tooltip="'Download'"
              />
              <Button
                icon="pi pi-eye"
                severity="secondary"
                text
                rounded
                size="small"
                @click="viewFile(data)"
                v-tooltip="'View Details'"
              />
              <Button
                icon="pi pi-trash"
                severity="danger"
                text
                rounded
                size="small"
                @click="confirmDelete(data)"
                v-tooltip="'Delete'"
              />
            </div>
          </template>
        </Column>
      </DataTable>
    </template>
  </div>
</template>

<style scoped>
.file-list-view {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.page-header h1 {
  margin: 0;
  font-size: 1.5rem;
  font-weight: 600;
}

.storage-warning {
  margin: 0;
}

.warning-content {
  display: flex;
  align-items: flex-start;
  gap: 1rem;
}

.warning-content i {
  font-size: 1.5rem;
  color: var(--p-orange-500);
}

.warning-content p {
  margin: 0.5rem 0 0 0;
  color: var(--p-text-muted-color);
}

.warning-content code {
  background-color: var(--p-surface-100);
  padding: 0.125rem 0.375rem;
  border-radius: 4px;
  font-size: 0.875rem;
}

.loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1rem;
  padding: 3rem;
  color: var(--p-text-muted-color);
}

.filters {
  display: flex;
  gap: 1rem;
  flex-wrap: wrap;
  padding: 1rem;
  background-color: var(--p-surface-0);
  border-radius: var(--p-border-radius);
  border: 1px solid var(--p-surface-200);
}

.filter-group {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.filter-group label {
  font-size: 0.75rem;
  font-weight: 500;
  color: var(--p-text-muted-color);
  text-transform: uppercase;
}

.filter-select {
  min-width: 150px;
}

.filter-input {
  width: 200px;
}

.files-table {
  background-color: var(--p-surface-0);
  border-radius: var(--p-border-radius);
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.5rem;
  padding: 2rem;
  color: var(--p-text-muted-color);
}

.empty-state i {
  font-size: 2rem;
}

.file-icon {
  font-size: 1.25rem;
  color: var(--p-primary-color);
}

.file-link {
  color: var(--p-primary-color);
  cursor: pointer;
  font-weight: 500;
}

.file-link:hover {
  text-decoration: underline;
}

.file-id {
  font-size: 0.75rem;
  background-color: var(--p-surface-100);
  padding: 0.125rem 0.375rem;
  border-radius: 4px;
}

.content-type {
  font-size: 0.875rem;
  color: var(--p-text-muted-color);
}

.no-refs {
  color: var(--p-orange-500);
}

.actions {
  display: flex;
  gap: 0.25rem;
}
</style>
