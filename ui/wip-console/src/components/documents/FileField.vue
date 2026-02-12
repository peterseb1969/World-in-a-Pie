<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import Button from 'primevue/button'
import FileUpload from 'primevue/fileupload'
import InputText from 'primevue/inputtext'
import ProgressBar from 'primevue/progressbar'
import Tag from 'primevue/tag'
import Message from 'primevue/message'
import Dialog from 'primevue/dialog'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Select from 'primevue/select'
import { fileStoreClient } from '@/api/client'
import { useUiStore } from '@/stores'
import type { FileEntity, FieldDefinition, FileFieldConfig } from '@/types'

const props = defineProps<{
  field: FieldDefinition
  modelValue: string | string[] | null
  disabled?: boolean
  errors?: string[]
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string | string[] | null]
}>()

const uiStore = useUiStore()

// Track uploaded files
const uploadedFiles = ref<FileEntity[]>([])
const loading = ref(false)
const uploading = ref(false)
const uploadProgress = ref(0)
const storageEnabled = ref<boolean | null>(null)

// File browse dialog
const showFileBrowser = ref(false)
const fileBrowseResults = ref<FileEntity[]>([])
const fileBrowseLoading = ref(false)
const fileBrowseSearch = ref('')
const fileBrowseType = ref<string | null>(null)

// Get file field config from metadata
const fileConfig = computed(() => {
  const config: FileFieldConfig | undefined = props.field.file_config
  return {
    allowedTypes: config?.allowed_types || ['*/*'],
    maxSizeMb: config?.max_size_mb || 10,
    multiple: config?.multiple || false
  }
})

// Determine if field allows multiple files
const isMultiple = computed(() => fileConfig.value.multiple || props.field.type === 'array')

// Get current file IDs
const currentFileIds = computed((): string[] => {
  if (!props.modelValue) return []
  if (Array.isArray(props.modelValue)) return props.modelValue
  return [props.modelValue]
})

// Accept string for file input
const acceptString = computed(() => {
  const types = fileConfig.value.allowedTypes
  if (types.includes('*/*') || types.length === 0) return '*'
  return types.join(',')
})

// Check if file storage is enabled
async function checkStorageEnabled() {
  try {
    storageEnabled.value = await fileStoreClient.isStorageEnabled()
  } catch {
    storageEnabled.value = false
  }
}

// Load existing file metadata
async function loadFiles() {
  if (currentFileIds.value.length === 0) {
    uploadedFiles.value = []
    return
  }

  loading.value = true
  try {
    const files: FileEntity[] = []
    for (const fileId of currentFileIds.value) {
      try {
        const file = await fileStoreClient.getFile(fileId)
        files.push(file)
      } catch (e) {
        console.warn(`Failed to load file ${fileId}:`, e)
      }
    }
    uploadedFiles.value = files
  } finally {
    loading.value = false
  }
}

// Handle file selection
async function onFileSelect(event: { files: File[] }) {
  const files = event.files
  if (!files || files.length === 0) return

  // Validate file count for single-file fields
  if (!isMultiple.value && (files.length > 1 || currentFileIds.value.length > 0)) {
    uiStore.showError('Upload Error', 'This field only allows a single file')
    return
  }

  uploading.value = true
  uploadProgress.value = 0

  try {
    const newFileIds: string[] = [...currentFileIds.value]
    const progressIncrement = 100 / files.length

    for (const file of files) {
      // Validate file size
      const maxBytes = fileConfig.value.maxSizeMb * 1024 * 1024
      if (file.size > maxBytes) {
        uiStore.showError('Upload Error', `File "${file.name}" exceeds maximum size of ${fileConfig.value.maxSizeMb}MB`)
        continue
      }

      // Upload file
      const uploaded = await fileStoreClient.uploadFile(file, {
        description: `Uploaded for field: ${props.field.name}`
      })

      newFileIds.push(uploaded.file_id)
      uploadedFiles.value.push(uploaded)
      uploadProgress.value += progressIncrement
    }

    // Update model value
    if (isMultiple.value) {
      emit('update:modelValue', newFileIds)
    } else {
      emit('update:modelValue', newFileIds[0] || null)
    }

    uploadProgress.value = 100
    uiStore.showSuccess('Upload Complete', `${files.length} file(s) uploaded successfully`)
  } catch (e) {
    uiStore.showError('Upload Failed', (e as Error).message)
  } finally {
    uploading.value = false
    uploadProgress.value = 0
  }
}

// Remove a file
function removeFile(fileId: string) {
  const newFileIds = currentFileIds.value.filter(id => id !== fileId)
  uploadedFiles.value = uploadedFiles.value.filter(f => f.file_id !== fileId)

  if (isMultiple.value) {
    emit('update:modelValue', newFileIds.length > 0 ? newFileIds : null)
  } else {
    emit('update:modelValue', null)
  }
}

// Open file browse dialog
async function openFileBrowser() {
  fileBrowseSearch.value = ''
  fileBrowseType.value = null
  fileBrowseResults.value = []
  showFileBrowser.value = true
  await browseFiles()
}

// Search/browse files
async function browseFiles() {
  fileBrowseLoading.value = true
  try {
    const params: Record<string, unknown> = {
      page_size: 50
    }
    if (fileBrowseType.value) {
      params.content_type = fileBrowseType.value
    }
    const result = await fileStoreClient.listFiles(params as Parameters<typeof fileStoreClient.listFiles>[0])
    // Client-side search filter (API doesn't support filename search)
    let files = result.items
    if (fileBrowseSearch.value.trim()) {
      const q = fileBrowseSearch.value.toLowerCase()
      files = files.filter(f =>
        f.filename.toLowerCase().includes(q) ||
        f.file_id.toLowerCase().includes(q) ||
        (f.metadata?.description || '').toLowerCase().includes(q)
      )
    }
    // Exclude already-linked files
    fileBrowseResults.value = files.filter(f => !currentFileIds.value.includes(f.file_id))
  } catch {
    fileBrowseResults.value = []
  } finally {
    fileBrowseLoading.value = false
  }
}

// Select a file from the browse dialog
function selectBrowseFile(file: FileEntity) {
  const newFileIds = [...currentFileIds.value, file.file_id]
  uploadedFiles.value.push(file)

  if (isMultiple.value) {
    emit('update:modelValue', newFileIds)
  } else {
    emit('update:modelValue', newFileIds[0] || null)
  }

  showFileBrowser.value = false
  uiStore.showSuccess('File Linked', `Linked: ${file.filename}`)
}

// Download a file — streams through the API to avoid mixed-content issues
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

// Watch for changes to model value
watch(() => props.modelValue, () => {
  loadFiles()
}, { immediate: true })

// Check storage enabled on mount
checkStorageEnabled()
</script>

<template>
  <div class="file-field">
    <!-- Storage not enabled warning -->
    <Message v-if="storageEnabled === false" severity="warn" :closable="false">
      File storage is not enabled. Contact your administrator.
    </Message>

    <template v-else>
      <!-- Loading state -->
      <div v-if="loading" class="loading-state">
        <i class="pi pi-spin pi-spinner"></i>
        Loading files...
      </div>

      <!-- Uploaded files list -->
      <div v-if="uploadedFiles.length > 0" class="file-list">
        <div
          v-for="file in uploadedFiles"
          :key="file.file_id"
          class="file-item"
        >
          <div class="file-icon">
            <i :class="['pi', getFileIcon(file.content_type)]"></i>
          </div>
          <div class="file-info">
            <div class="file-name">{{ file.filename }}</div>
            <div class="file-meta">
              <span class="file-size">{{ formatFileSize(file.size_bytes) }}</span>
              <span class="file-type">{{ file.content_type }}</span>
              <Tag
                :value="file.status"
                :severity="getStatusSeverity(file.status)"
                size="small"
              />
            </div>
          </div>
          <div class="file-actions">
            <Button
              icon="pi pi-download"
              severity="secondary"
              text
              rounded
              size="small"
              @click="downloadFile(file)"
              v-tooltip="'Download'"
            />
            <Button
              icon="pi pi-times"
              severity="danger"
              text
              rounded
              size="small"
              @click="removeFile(file.file_id)"
              :disabled="disabled"
              v-tooltip="'Remove'"
            />
          </div>
        </div>
      </div>

      <!-- Upload progress -->
      <ProgressBar
        v-if="uploading"
        :value="uploadProgress"
        :showValue="true"
        class="upload-progress"
      />

      <!-- Upload / Link section -->
      <div v-if="!disabled && (isMultiple || currentFileIds.length === 0)" class="upload-section">
        <FileUpload
          mode="basic"
          :accept="acceptString"
          :multiple="isMultiple"
          :auto="true"
          :choose-label="isMultiple ? 'Add Files' : 'Upload File'"
          choose-icon="pi pi-upload"
          custom-upload
          @select="onFileSelect"
          :disabled="uploading"
        />
        <div class="upload-hints">
          <small v-if="fileConfig.allowedTypes.length > 0 && !fileConfig.allowedTypes.includes('*/*')">
            Allowed types: {{ fileConfig.allowedTypes.join(', ') }}
          </small>
          <small>Max size: {{ fileConfig.maxSizeMb }} MB</small>
        </div>

        <div class="link-divider">
          <span>or browse existing files</span>
        </div>

        <Button
          label="Browse Files"
          icon="pi pi-folder-open"
          severity="secondary"
          outlined
          size="small"
          @click="openFileBrowser"
        />
      </div>

      <!-- Empty state -->
      <div v-if="!loading && uploadedFiles.length === 0 && !uploading" class="empty-state">
        <i class="pi pi-file"></i>
        <span>No files uploaded</span>
      </div>
    </template>

    <!-- Errors -->
    <div v-if="errors && errors.length > 0" class="field-errors">
      <small v-for="err in errors" :key="err" class="error-text">{{ err }}</small>
    </div>

    <!-- File Browse Dialog -->
    <Dialog
      v-model:visible="showFileBrowser"
      header="Browse Files"
      :style="{ width: '750px' }"
      modal
    >
      <div class="file-browse">
        <div class="file-browse-filters">
          <InputText
            v-model="fileBrowseSearch"
            placeholder="Search by filename..."
            class="file-browse-search"
            @keyup.enter="browseFiles"
          />
          <Select
            v-model="fileBrowseType"
            :options="[
              { label: 'All types', value: null },
              { label: 'Images', value: 'image/' },
              { label: 'PDFs', value: 'application/pdf' },
              { label: 'Documents', value: 'application/' },
              { label: 'Text', value: 'text/' }
            ]"
            optionLabel="label"
            optionValue="value"
            placeholder="File type"
            class="file-browse-type"
            @change="browseFiles"
          />
          <Button icon="pi pi-search" @click="browseFiles" :loading="fileBrowseLoading" />
        </div>
        <small class="file-browse-hint">Click a row to select the file</small>
        <DataTable
          :value="fileBrowseResults"
          :loading="fileBrowseLoading"
          size="small"
          @row-click="(e: any) => selectBrowseFile(e.data)"
          :pt="{ bodyRow: { style: 'cursor: pointer' } }"
          scrollable
          scrollHeight="350px"
        >
          <Column header="File">
            <template #body="{ data }">
              <div class="file-browse-name">
                <i :class="['pi', getFileIcon(data.content_type)]"></i>
                <div>
                  <span class="fb-filename">{{ data.filename }}</span>
                  <span class="fb-desc" v-if="data.metadata?.description">{{ data.metadata.description }}</span>
                </div>
              </div>
            </template>
          </Column>
          <Column field="content_type" header="Type" style="width: 120px">
            <template #body="{ data }">
              <span class="fb-type">{{ data.content_type }}</span>
            </template>
          </Column>
          <Column header="Size" style="width: 80px">
            <template #body="{ data }">
              <span class="fb-size">{{ formatFileSize(data.size_bytes) }}</span>
            </template>
          </Column>
          <Column field="file_id" header="ID" style="width: 110px">
            <template #body="{ data }">
              <code class="fb-id">{{ data.file_id.slice(0, 12) }}</code>
            </template>
          </Column>
          <template #empty>
            <div style="text-align: center; padding: 1rem; color: var(--p-text-muted-color)">
              {{ fileBrowseLoading ? 'Loading...' : 'No files found' }}
            </div>
          </template>
        </DataTable>
      </div>
    </Dialog>
  </div>
</template>

<style scoped>
.file-field {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.loading-state {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
}

.file-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.file-item {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.75rem;
  border: 1px solid var(--p-surface-200);
  border-radius: var(--p-border-radius);
  background-color: var(--p-surface-50);
}

.file-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: var(--p-border-radius);
  background-color: var(--p-primary-100);
  color: var(--p-primary-600);
}

.file-icon i {
  font-size: 1.25rem;
}

.file-info {
  flex: 1;
  min-width: 0;
}

.file-name {
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.file-meta {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-top: 0.25rem;
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
}

.file-actions {
  display: flex;
  gap: 0.25rem;
}

.upload-progress {
  height: 6px;
}

.upload-section {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.upload-hints {
  display: flex;
  gap: 1rem;
  color: var(--p-text-muted-color);
  font-size: 0.75rem;
}

.link-divider {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  color: var(--p-text-muted-color);
  font-size: 0.75rem;
}

.link-divider::before,
.link-divider::after {
  content: '';
  flex: 1;
  height: 1px;
  background-color: var(--p-surface-200);
}

.file-browse {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.file-browse-filters {
  display: flex;
  gap: 0.5rem;
  align-items: center;
}

.file-browse-search {
  flex: 1;
}

.file-browse-type {
  width: 160px;
}

.file-browse-hint {
  color: var(--p-text-muted-color);
}

.file-browse-name {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.file-browse-name i {
  font-size: 1.1rem;
  color: var(--p-primary-600);
}

.file-browse-name div {
  display: flex;
  flex-direction: column;
}

.fb-filename {
  font-weight: 500;
  font-size: 0.875rem;
}

.fb-desc {
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
}

.fb-type {
  font-size: 0.8rem;
  color: var(--p-text-muted-color);
}

.fb-size {
  font-size: 0.8rem;
}

.fb-id {
  font-size: 0.75rem;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.5rem;
  padding: 1.5rem;
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
  border: 1px dashed var(--p-surface-300);
  border-radius: var(--p-border-radius);
}

.empty-state i {
  font-size: 1.5rem;
}

.field-errors {
  display: flex;
  flex-direction: column;
  gap: 0.125rem;
}

.error-text {
  color: var(--p-red-500);
  font-size: 0.75rem;
}
</style>
