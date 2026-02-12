<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useConfirm } from 'primevue/useconfirm'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Button from 'primevue/button'
import InputNumber from 'primevue/inputnumber'
import Tag from 'primevue/tag'
import Message from 'primevue/message'
import ProgressSpinner from 'primevue/progressspinner'
import { fileStoreClient } from '@/api/client'
import { useUiStore } from '@/stores'
import TruncatedId from '@/components/common/TruncatedId.vue'
import type { FileEntity } from '@/types'

const router = useRouter()
const confirm = useConfirm()
const uiStore = useUiStore()

// State
const orphans = ref<FileEntity[]>([])
const loading = ref(false)
const storageEnabled = ref<boolean | null>(null)
const selectedFiles = ref<FileEntity[]>([])
const deleting = ref(false)

// Filters
const olderThanHours = ref(0)  // 0 = show all orphans
const limit = ref(100)

// Check storage status
async function checkStorageEnabled() {
  try {
    storageEnabled.value = await fileStoreClient.isStorageEnabled()
  } catch {
    storageEnabled.value = false
  }
}

// Load orphan files
async function loadOrphans() {
  if (!storageEnabled.value) return

  loading.value = true
  try {
    orphans.value = await fileStoreClient.listOrphans({
      older_than_hours: olderThanHours.value,
      limit: limit.value
    })
  } catch (e) {
    uiStore.showError('Error', (e as Error).message)
  } finally {
    loading.value = false
  }
}

// View file details
function viewFile(file: FileEntity) {
  router.push(`/files/${file.file_id}`)
}

// Download file — streams through the API to avoid mixed-content issues
async function downloadFile(file: FileEntity) {
  try {
    const blob = await fileStoreClient.downloadFileContent(file.file_id)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = file.filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  } catch (e) {
    uiStore.showError('Download Failed', (e as Error).message)
  }
}

// Delete single file
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
        loadOrphans()
      } catch (e) {
        uiStore.showError('Delete Failed', (e as Error).message)
      }
    }
  })
}

// Bulk delete selected files
function confirmBulkDelete() {
  if (selectedFiles.value.length === 0) return

  confirm.require({
    message: `Delete ${selectedFiles.value.length} selected file(s)?`,
    header: 'Bulk Delete',
    icon: 'pi pi-exclamation-triangle',
    acceptClass: 'p-button-danger',
    accept: async () => {
      deleting.value = true
      try {
        const response = await fileStoreClient.bulkDelete({
          file_ids: selectedFiles.value.map(f => f.file_id)
        })
        uiStore.showSuccess(
          'Bulk Delete Complete',
          `Deleted ${response.deleted} file(s), ${response.failed} failed`
        )
        selectedFiles.value = []
        loadOrphans()
      } catch (e) {
        uiStore.showError('Bulk Delete Failed', (e as Error).message)
      } finally {
        deleting.value = false
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

// Get file icon
function getFileIcon(contentType: string): string {
  if (contentType.startsWith('image/')) return 'pi-image'
  if (contentType.startsWith('video/')) return 'pi-video'
  if (contentType.startsWith('audio/')) return 'pi-volume-up'
  if (contentType === 'application/pdf') return 'pi-file-pdf'
  return 'pi-file'
}

// Format date
function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleString()
}

// Calculate age in hours
function getAgeHours(uploadedAt: string): number {
  const now = new Date()
  const uploaded = new Date(uploadedAt)
  return Math.floor((now.getTime() - uploaded.getTime()) / (1000 * 60 * 60))
}

onMounted(async () => {
  await checkStorageEnabled()
  if (storageEnabled.value) {
    loadOrphans()
  }
})
</script>

<template>
  <div class="orphan-files-view">
    <div class="page-header">
      <div class="header-left">
        <Button
          icon="pi pi-arrow-left"
          severity="secondary"
          text
          rounded
          @click="router.push('/files')"
        />
        <h1>Orphan Files</h1>
      </div>
    </div>

    <!-- Info message -->
    <Message severity="info" :closable="false">
      <div class="info-content">
        <strong>What are orphan files?</strong>
        <p>
          Orphan files are uploaded files that are not referenced by any active document.
          They may have been uploaded but never used, or their referencing documents were deleted.
          You can safely delete orphan files to free up storage space.
        </p>
      </div>
    </Message>

    <!-- Storage not enabled warning -->
    <Message v-if="storageEnabled === false" severity="warn" :closable="false">
      File storage is not enabled.
    </Message>

    <!-- Loading storage check -->
    <div v-else-if="storageEnabled === null" class="loading-state">
      <ProgressSpinner />
      <span>Checking storage status...</span>
    </div>

    <!-- Main content -->
    <template v-else>
      <!-- Filters and actions -->
      <div class="toolbar">
        <div class="filters">
          <div class="filter-group">
            <label>Older than (hours)</label>
            <InputNumber
              v-model="olderThanHours"
              :min="0"
              :max="720"
              showButtons
              buttonLayout="horizontal"
              incrementButtonIcon="pi pi-plus"
              decrementButtonIcon="pi pi-minus"
              class="filter-input"
              placeholder="0 = all"
            />
          </div>
          <div class="filter-group">
            <label>Limit</label>
            <InputNumber
              v-model="limit"
              :min="10"
              :max="1000"
              :step="10"
              showButtons
              buttonLayout="horizontal"
              incrementButtonIcon="pi pi-plus"
              decrementButtonIcon="pi pi-minus"
              class="filter-input"
            />
          </div>
          <Button
            label="Refresh"
            icon="pi pi-refresh"
            severity="secondary"
            @click="loadOrphans"
            :loading="loading"
          />
        </div>
        <div class="actions">
          <Button
            v-if="selectedFiles.length > 0"
            :label="`Delete ${selectedFiles.length} Selected`"
            icon="pi pi-trash"
            severity="danger"
            @click="confirmBulkDelete"
            :loading="deleting"
          />
        </div>
      </div>

      <!-- Summary -->
      <div class="summary" v-if="!loading">
        <Tag
          :value="`${orphans.length} orphan file(s) found`"
          :severity="orphans.length > 0 ? 'warn' : 'success'"
        />
        <span v-if="orphans.length > 0" class="total-size">
          Total size: {{ formatFileSize(orphans.reduce((sum, f) => sum + f.size_bytes, 0)) }}
        </span>
      </div>

      <!-- Data Table -->
      <DataTable
        v-model:selection="selectedFiles"
        :value="orphans"
        :loading="loading"
        stripedRows
        showGridlines
        size="small"
        dataKey="file_id"
        class="orphans-table"
      >
        <template #empty>
          <div class="empty-state">
            <i class="pi pi-check-circle"></i>
            <span>No orphan files found</span>
            <small>All files are properly referenced by documents</small>
          </div>
        </template>

        <Column selectionMode="multiple" style="width: 3rem" />

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
            <TruncatedId :id="data.file_id" :length="11" />
          </template>
        </Column>

        <Column field="content_type" header="Type" style="width: 150px" />

        <Column field="size_bytes" header="Size" style="width: 100px">
          <template #body="{ data }">
            {{ formatFileSize(data.size_bytes) }}
          </template>
        </Column>

        <Column field="uploaded_at" header="Age" style="width: 100px">
          <template #body="{ data }">
            <span class="age">{{ getAgeHours(data.uploaded_at) }}h</span>
          </template>
        </Column>

        <Column field="uploaded_at" header="Uploaded" style="width: 160px">
          <template #body="{ data }">
            {{ formatDate(data.uploaded_at) }}
          </template>
        </Column>

        <Column header="Actions" style="width: 100px">
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
.orphan-files-view {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.page-header h1 {
  margin: 0;
  font-size: 1.5rem;
  font-weight: 600;
}

.info-content {
  line-height: 1.5;
}

.info-content p {
  margin: 0.5rem 0 0 0;
  color: var(--p-text-muted-color);
}

.loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1rem;
  padding: 3rem;
  color: var(--p-text-muted-color);
}

.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 1rem;
  flex-wrap: wrap;
}

.filters {
  display: flex;
  gap: 1rem;
  align-items: flex-end;
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

.filter-input {
  width: 120px;
}

.summary {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.total-size {
  font-size: 0.875rem;
  color: var(--p-text-muted-color);
}

.orphans-table {
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
  color: var(--p-green-500);
}

.empty-state small {
  font-size: 0.875rem;
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

.age {
  color: var(--p-orange-600);
  font-weight: 500;
}

.actions {
  display: flex;
  gap: 0.25rem;
}
</style>
