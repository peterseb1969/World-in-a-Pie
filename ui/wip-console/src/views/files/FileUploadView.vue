<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import Card from 'primevue/card'
import Button from 'primevue/button'
import FileUpload from 'primevue/fileupload'
import InputText from 'primevue/inputtext'
import Textarea from 'primevue/textarea'
import Chips from 'primevue/chips'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Tag from 'primevue/tag'
import ProgressBar from 'primevue/progressbar'
import Message from 'primevue/message'
import ProgressSpinner from 'primevue/progressspinner'
import { fileStoreClient } from '@/api/client'
import { useUiStore } from '@/stores'
import type { FileEntity } from '@/types'

const router = useRouter()
const uiStore = useUiStore()

// State
const storageEnabled = ref<boolean | null>(null)
const uploading = ref(false)
const uploadProgress = ref(0)
const uploadResults = ref<Array<{ file: File; status: 'success' | 'error'; entity?: FileEntity; error?: string }>>([])

// Metadata fields
const description = ref('')
const category = ref('')
const tags = ref<string[]>([])
const allowedTemplates = ref<string[]>([])

// Check storage status
async function checkStorageEnabled() {
  try {
    storageEnabled.value = await fileStoreClient.isStorageEnabled()
  } catch {
    storageEnabled.value = false
  }
}

// Handle file selection
async function onFileSelect(event: { files: File[] }) {
  const files = event.files
  if (!files || files.length === 0) return

  uploading.value = true
  uploadProgress.value = 0
  uploadResults.value = []

  const progressIncrement = 100 / files.length

  for (const file of files) {
    try {
      const entity = await fileStoreClient.uploadFile(file, {
        description: description.value || undefined,
        tags: tags.value.length > 0 ? tags.value : undefined,
        category: category.value || undefined,
        allowed_templates: allowedTemplates.value.length > 0 ? allowedTemplates.value : undefined
      })
      uploadResults.value.push({ file, status: 'success', entity })
    } catch (e) {
      uploadResults.value.push({ file, status: 'error', error: (e as Error).message })
    }
    uploadProgress.value += progressIncrement
  }

  uploadProgress.value = 100
  uploading.value = false

  const successCount = uploadResults.value.filter(r => r.status === 'success').length
  const failCount = uploadResults.value.filter(r => r.status === 'error').length

  if (failCount === 0) {
    uiStore.showSuccess('Upload Complete', `${successCount} file(s) uploaded successfully`)
  } else {
    uiStore.showWarn('Upload Partial', `${successCount} uploaded, ${failCount} failed`)
  }
}

// View uploaded file
function viewFile(fileId: string) {
  router.push(`/files/${fileId}`)
}

// Reset form
function resetForm() {
  description.value = ''
  category.value = ''
  tags.value = []
  allowedTemplates.value = []
  uploadResults.value = []
  uploadProgress.value = 0
}

// Format file size
function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

onMounted(() => {
  checkStorageEnabled()
})
</script>

<template>
  <div class="file-upload-view">
    <div class="page-header">
      <div class="header-left">
        <Button
          icon="pi pi-arrow-left"
          severity="secondary"
          text
          rounded
          @click="router.push('/files')"
        />
        <h1>Upload Files</h1>
      </div>
    </div>

    <!-- Storage not enabled warning -->
    <Message v-if="storageEnabled === false" severity="warn" :closable="false">
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
      <div class="content-grid">
        <!-- Metadata card -->
        <Card class="metadata-card">
          <template #title>Metadata (Optional)</template>
          <template #subtitle>Apply these metadata to all uploaded files</template>
          <template #content>
            <div class="form-fields">
              <div class="form-group">
                <label>Description</label>
                <Textarea
                  v-model="description"
                  rows="2"
                  placeholder="File description"
                  class="w-full"
                  :disabled="uploading"
                />
              </div>
              <div class="form-group">
                <label>Category</label>
                <InputText
                  v-model="category"
                  placeholder="e.g., invoices, photos, contracts"
                  class="w-full"
                  :disabled="uploading"
                />
              </div>
              <div class="form-group">
                <label>Tags</label>
                <Chips
                  v-model="tags"
                  placeholder="Add tags and press Enter"
                  class="w-full"
                  :disabled="uploading"
                />
              </div>
              <div class="form-group">
                <label>Allowed Templates</label>
                <Chips
                  v-model="allowedTemplates"
                  placeholder="Template codes (optional)"
                  class="w-full"
                  :disabled="uploading"
                />
                <small class="help-text">
                  Restrict which templates can reference this file
                </small>
              </div>
            </div>
          </template>
        </Card>

        <!-- Upload card -->
        <Card class="upload-card">
          <template #title>Select Files</template>
          <template #content>
            <div class="upload-area">
              <FileUpload
                name="files"
                mode="advanced"
                :multiple="true"
                :maxFileSize="100000000"
                customUpload
                :auto="false"
                @select="onFileSelect"
                :disabled="uploading"
                chooseLabel="Select Files"
                :showUploadButton="false"
                :showCancelButton="false"
              >
                <template #empty>
                  <div class="upload-empty">
                    <i class="pi pi-cloud-upload"></i>
                    <p>Drag and drop files here or click to browse</p>
                    <small>Maximum file size: 100 MB</small>
                  </div>
                </template>
              </FileUpload>

              <!-- Upload progress -->
              <ProgressBar
                v-if="uploading"
                :value="uploadProgress"
                :showValue="true"
                class="upload-progress"
              />
            </div>
          </template>
        </Card>

        <!-- Results card -->
        <Card v-if="uploadResults.length > 0" class="results-card">
          <template #title>Upload Results</template>
          <template #content>
            <div class="results-summary">
              <Tag
                :value="`${uploadResults.filter(r => r.status === 'success').length} Uploaded`"
                severity="success"
              />
              <Tag
                v-if="uploadResults.filter(r => r.status === 'error').length > 0"
                :value="`${uploadResults.filter(r => r.status === 'error').length} Failed`"
                severity="danger"
              />
              <Button
                label="Clear"
                severity="secondary"
                text
                size="small"
                @click="resetForm"
              />
            </div>

            <DataTable :value="uploadResults" stripedRows size="small" class="results-table">
              <Column field="file.name" header="Filename" style="min-width: 200px">
                <template #body="{ data }">
                  <a
                    v-if="data.status === 'success'"
                    class="file-link"
                    @click="viewFile(data.entity.file_id)"
                  >
                    {{ data.file.name }}
                  </a>
                  <span v-else>{{ data.file.name }}</span>
                </template>
              </Column>

              <Column header="File ID" style="width: 140px">
                <template #body="{ data }">
                  <code v-if="data.entity" class="file-id">{{ data.entity.file_id }}</code>
                  <span v-else>-</span>
                </template>
              </Column>

              <Column field="file.size" header="Size" style="width: 100px">
                <template #body="{ data }">
                  {{ formatFileSize(data.file.size) }}
                </template>
              </Column>

              <Column field="status" header="Status" style="width: 100px">
                <template #body="{ data }">
                  <Tag
                    :value="data.status"
                    :severity="data.status === 'success' ? 'success' : 'danger'"
                  />
                </template>
              </Column>

              <Column header="Error" style="min-width: 150px">
                <template #body="{ data }">
                  <span v-if="data.error" class="error-text">{{ data.error }}</span>
                  <span v-else>-</span>
                </template>
              </Column>
            </DataTable>
          </template>
        </Card>
      </div>
    </template>
  </div>
</template>

<style scoped>
.file-upload-view {
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

.content-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1.5rem;
}

.metadata-card {
  grid-column: span 1;
}

.upload-card {
  grid-column: span 1;
}

.results-card {
  grid-column: span 2;
}

.form-fields {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
}

.form-group label {
  font-size: 0.875rem;
  font-weight: 500;
}

.help-text {
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
}

.w-full {
  width: 100%;
}

.upload-area {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.upload-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.5rem;
  padding: 2rem;
  color: var(--p-text-muted-color);
}

.upload-empty i {
  font-size: 3rem;
  color: var(--p-primary-color);
}

.upload-empty p {
  margin: 0;
}

.upload-progress {
  height: 8px;
}

.results-summary {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 1rem;
}

.results-table {
  font-size: 0.875rem;
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

.error-text {
  color: var(--p-red-500);
  font-size: 0.8125rem;
}

@media (max-width: 768px) {
  .content-grid {
    grid-template-columns: 1fr;
  }

  .metadata-card,
  .upload-card,
  .results-card {
    grid-column: span 1;
  }
}
</style>
