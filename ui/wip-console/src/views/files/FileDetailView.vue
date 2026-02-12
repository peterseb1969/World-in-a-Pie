<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useConfirm } from 'primevue/useconfirm'
import Card from 'primevue/card'
import Button from 'primevue/button'
import Tag from 'primevue/tag'
import InputText from 'primevue/inputtext'
import Textarea from 'primevue/textarea'
import Chips from 'primevue/chips'
import Message from 'primevue/message'
import ProgressSpinner from 'primevue/progressspinner'
import Panel from 'primevue/panel'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Paginator from 'primevue/paginator'
import { fileStoreClient } from '@/api/client'
import { useUiStore } from '@/stores'
import type { FileEntity } from '@/types'

const route = useRoute()
const router = useRouter()
const confirm = useConfirm()
const uiStore = useUiStore()

const fileId = computed(() => route.params.id as string)

// State
const file = ref<FileEntity | null>(null)
const loading = ref(true)
const saving = ref(false)
const editMode = ref(false)

// Editable fields
const editDescription = ref('')
const editTags = ref<string[]>([])
const editCategory = ref('')

// Referencing documents state
const referencingDocs = ref<Array<{
  document_id: string
  template_id: string
  template_code: string | null
  field_path: string
  status: string
  created_at: string | null
}>>([])
const referencingDocsTotal = ref(0)
const referencingDocsPage = ref(1)
const referencingDocsPageSize = ref(10)
const referencingDocsPages = ref(1)
const referencingDocsLoading = ref(false)

async function loadReferencingDocs() {
  if (!file.value || file.value.reference_count === 0) return

  referencingDocsLoading.value = true
  try {
    const response = await fileStoreClient.getFileDocuments(
      fileId.value,
      referencingDocsPage.value,
      referencingDocsPageSize.value
    )
    referencingDocs.value = response.items
    referencingDocsTotal.value = response.total
    referencingDocsPages.value = response.pages
  } catch (e) {
    console.error('Failed to load referencing documents:', e)
  } finally {
    referencingDocsLoading.value = false
  }
}

function onReferencingDocsPage(event: { page: number; rows: number }) {
  referencingDocsPage.value = event.page + 1  // PrimeVue Paginator is 0-indexed
  referencingDocsPageSize.value = event.rows
  loadReferencingDocs()
}

function navigateToDocument(documentId: string) {
  router.push(`/documents/${documentId}`)
}

// Load file
async function loadFile() {
  loading.value = true
  try {
    file.value = await fileStoreClient.getFile(fileId.value)
    // Initialize edit values
    editDescription.value = file.value.metadata.description || ''
    editTags.value = [...file.value.metadata.tags]
    editCategory.value = file.value.metadata.category || ''
  } catch (e) {
    uiStore.showError('Error', (e as Error).message)
    router.push('/files')
  } finally {
    loading.value = false
  }
}

// Save changes
async function saveChanges() {
  if (!file.value) return

  saving.value = true
  try {
    file.value = await fileStoreClient.updateMetadata(fileId.value, {
      description: editDescription.value || undefined,
      tags: editTags.value.length > 0 ? editTags.value : undefined,
      category: editCategory.value || undefined
    })
    editMode.value = false
    uiStore.showSuccess('Saved', 'File metadata updated')
  } catch (e) {
    uiStore.showError('Save Failed', (e as Error).message)
  } finally {
    saving.value = false
  }
}

// Cancel editing
function cancelEdit() {
  if (file.value) {
    editDescription.value = file.value.metadata.description || ''
    editTags.value = [...file.value.metadata.tags]
    editCategory.value = file.value.metadata.category || ''
  }
  editMode.value = false
}

// Download file — streams through the API to avoid mixed-content (HTTPS→HTTP) issues
// with direct MinIO pre-signed URLs
async function downloadFile() {
  if (!file.value) return
  try {
    const blob = await fileStoreClient.downloadFileContent(file.value.file_id)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = file.value.filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  } catch (e) {
    uiStore.showError('Download Failed', (e as Error).message)
  }
}

// Delete file
function confirmDelete() {
  if (!file.value) return

  confirm.require({
    message: `Are you sure you want to delete "${file.value.filename}"?`,
    header: 'Delete File',
    icon: 'pi pi-exclamation-triangle',
    acceptClass: 'p-button-danger',
    accept: async () => {
      try {
        await fileStoreClient.deleteFile(file.value!.file_id)
        uiStore.showSuccess('Deleted', 'File has been deleted')
        router.push('/files')
      } catch (e) {
        uiStore.showError('Delete Failed', (e as Error).message)
      }
    }
  })
}

// Hard delete file
function confirmHardDelete() {
  if (!file.value) return

  confirm.require({
    message: `PERMANENTLY delete "${file.value.filename}"? This cannot be undone.`,
    header: 'Permanent Delete',
    icon: 'pi pi-exclamation-triangle',
    acceptClass: 'p-button-danger',
    accept: async () => {
      try {
        await fileStoreClient.hardDeleteFile(file.value!.file_id)
        uiStore.showSuccess('Deleted', 'File has been permanently deleted')
        router.push('/files')
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
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
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
function formatDate(dateStr: string | null): string {
  if (!dateStr) return '-'
  return new Date(dateStr).toLocaleString()
}

// Check if file is an image for preview
const isImage = computed(() => file.value?.content_type.startsWith('image/'))

// Get preview URL (if image) — fetches blob through API to avoid mixed-content issues
const previewUrl = ref<string | null>(null)
async function loadPreview() {
  if (!file.value || !isImage.value) return
  try {
    const blob = await fileStoreClient.downloadFileContent(file.value.file_id)
    previewUrl.value = URL.createObjectURL(blob)
  } catch {
    // Ignore preview errors
  }
}

onMounted(async () => {
  await loadFile()
  if (isImage.value) {
    loadPreview()
  }
  loadReferencingDocs()
})
</script>

<template>
  <div class="file-detail-view">
    <!-- Loading state -->
    <div v-if="loading" class="loading-state">
      <ProgressSpinner />
      <span>Loading file...</span>
    </div>

    <!-- File details -->
    <template v-else-if="file">
      <!-- Header -->
      <div class="page-header">
        <div class="header-left">
          <Button
            icon="pi pi-arrow-left"
            severity="secondary"
            text
            rounded
            @click="router.push('/files')"
          />
          <div class="header-title">
            <i :class="['pi', getFileIcon(file.content_type), 'file-icon']"></i>
            <h1>{{ file.filename }}</h1>
          </div>
        </div>
        <div class="header-actions">
          <Button
            label="Download"
            icon="pi pi-download"
            severity="secondary"
            @click="downloadFile"
          />
          <Button
            v-if="!editMode"
            label="Edit"
            icon="pi pi-pencil"
            @click="editMode = true"
          />
          <template v-else>
            <Button
              label="Cancel"
              severity="secondary"
              text
              @click="cancelEdit"
            />
            <Button
              label="Save"
              icon="pi pi-check"
              @click="saveChanges"
              :loading="saving"
            />
          </template>
        </div>
      </div>

      <!-- Status warnings -->
      <Message v-if="file.status === 'orphan'" severity="warn" :closable="false">
        This file is not referenced by any document. It may be deleted during cleanup.
      </Message>
      <Message v-if="file.status === 'inactive'" severity="error" :closable="false">
        This file has been soft-deleted and is no longer accessible to documents.
      </Message>

      <div class="content-grid">
        <!-- Main info card -->
        <Card class="info-card">
          <template #title>File Information</template>
          <template #content>
            <div class="info-grid">
              <div class="info-item">
                <span class="label">File ID</span>
                <code class="value">{{ file.file_id }}</code>
              </div>
              <div class="info-item">
                <span class="label">Status</span>
                <Tag :value="file.status" :severity="getStatusSeverity(file.status)" />
              </div>
              <div class="info-item">
                <span class="label">Content Type</span>
                <span class="value">{{ file.content_type }}</span>
              </div>
              <div class="info-item">
                <span class="label">Size</span>
                <span class="value">{{ formatFileSize(file.size_bytes) }}</span>
              </div>
              <div class="info-item">
                <span class="label">References</span>
                <span class="value" :class="{ 'no-refs': file.reference_count === 0 }">
                  {{ file.reference_count }} document(s)
                </span>
              </div>
              <div class="info-item">
                <span class="label">Checksum (SHA-256)</span>
                <code class="value checksum">{{ file.checksum }}</code>
              </div>
              <div class="info-item">
                <span class="label">Storage Key</span>
                <code class="value">{{ file.storage_key }}</code>
              </div>
              <div class="info-item">
                <span class="label">Uploaded</span>
                <span class="value">{{ formatDate(file.uploaded_at) }}</span>
              </div>
              <div class="info-item">
                <span class="label">Uploaded By</span>
                <span class="value">{{ file.uploaded_by || '-' }}</span>
              </div>
              <div class="info-item">
                <span class="label">Updated</span>
                <span class="value">{{ formatDate(file.updated_at) }}</span>
              </div>
            </div>
          </template>
        </Card>

        <!-- Metadata card -->
        <Card class="metadata-card">
          <template #title>
            <span class="metadata-title">Metadata <Tag value="User-defined" severity="secondary" class="non-schema-badge" /></span>
          </template>
          <template #content>
            <div v-if="!editMode" class="metadata-view">
              <div class="metadata-item">
                <span class="label">Description</span>
                <p class="value">{{ file.metadata.description || 'No description' }}</p>
              </div>
              <div class="metadata-item">
                <span class="label">Category</span>
                <span class="value">{{ file.metadata.category || '-' }}</span>
              </div>
              <div class="metadata-item">
                <span class="label">Tags</span>
                <div class="tags-list" v-if="file.metadata.tags.length > 0">
                  <Tag v-for="tag in file.metadata.tags" :key="tag" :value="tag" severity="secondary" />
                </div>
                <span v-else class="value">No tags</span>
              </div>
              <div class="metadata-item" v-if="file.allowed_templates?.length">
                <span class="label">Allowed Templates</span>
                <div class="tags-list">
                  <Tag v-for="tpl in file.allowed_templates" :key="tpl" :value="tpl" severity="info" />
                </div>
              </div>
            </div>

            <div v-else class="metadata-edit">
              <div class="form-group">
                <label>Description</label>
                <Textarea
                  v-model="editDescription"
                  rows="3"
                  placeholder="File description"
                  class="w-full"
                />
              </div>
              <div class="form-group">
                <label>Category</label>
                <InputText
                  v-model="editCategory"
                  placeholder="Category"
                  class="w-full"
                />
              </div>
              <div class="form-group">
                <label>Tags</label>
                <Chips
                  v-model="editTags"
                  placeholder="Add tags"
                  class="w-full"
                />
              </div>
            </div>
          </template>
        </Card>

        <!-- Referencing Documents card -->
        <Card v-if="file.reference_count > 0" class="referencing-docs-card">
          <template #title>
            <div class="referencing-docs-title">
              <span>Referencing Documents</span>
              <Tag :value="`${referencingDocsTotal}`" severity="secondary" />
            </div>
          </template>
          <template #content>
            <div v-if="referencingDocsLoading" class="loading-inline">
              <ProgressSpinner style="width: 24px; height: 24px" />
              <span>Loading documents...</span>
            </div>
            <template v-else>
              <DataTable
                :value="referencingDocs"
                size="small"
                class="referencing-docs-table"
                @row-click="(e) => navigateToDocument(e.data.document_id)"
                :pt="{ bodyRow: { style: 'cursor: pointer' } }"
              >
                <Column field="document_id" header="Document ID" style="width: 240px">
                  <template #body="{ data }">
                    <code class="doc-id">{{ data.document_id }}</code>
                  </template>
                </Column>
                <Column field="template_id" header="Template" style="width: 180px">
                  <template #body="{ data }">
                    <span>{{ data.template_code || data.template_id }}</span>
                  </template>
                </Column>
                <Column field="field_path" header="Field">
                  <template #body="{ data }">
                    <code class="field-path">{{ data.field_path }}</code>
                  </template>
                </Column>
                <Column field="status" header="Status" style="width: 100px">
                  <template #body="{ data }">
                    <Tag :value="data.status" :severity="data.status === 'active' ? 'success' : 'danger'" size="small" />
                  </template>
                </Column>
                <Column field="created_at" header="Created" style="width: 160px">
                  <template #body="{ data }">
                    <span class="date-value">{{ formatDate(data.created_at) }}</span>
                  </template>
                </Column>
                <template #empty>
                  <div class="empty-state-inline">No referencing documents found</div>
                </template>
              </DataTable>
              <Paginator
                v-if="referencingDocsTotal > referencingDocsPageSize"
                :rows="referencingDocsPageSize"
                :totalRecords="referencingDocsTotal"
                :first="(referencingDocsPage - 1) * referencingDocsPageSize"
                :rowsPerPageOptions="[10, 25, 50]"
                @page="onReferencingDocsPage"
              />
            </template>
          </template>
        </Card>

        <!-- Preview card (for images) -->
        <Card v-if="isImage && previewUrl" class="preview-card">
          <template #title>Preview</template>
          <template #content>
            <div class="preview-container">
              <img :src="previewUrl" :alt="file.filename" class="preview-image" />
            </div>
          </template>
        </Card>

        <!-- Raw JSON -->
        <Panel header="Raw JSON" toggleable :collapsed="true" class="raw-json-panel">
          <div class="raw-json">
            <pre>{{ JSON.stringify(file, null, 2) }}</pre>
          </div>
        </Panel>

        <!-- Danger zone -->
        <Card class="danger-card">
          <template #title>Danger Zone</template>
          <template #content>
            <div class="danger-actions">
              <div class="danger-item" v-if="file.status !== 'inactive'">
                <div class="danger-info">
                  <strong>Soft Delete</strong>
                  <p>Mark this file as inactive. Documents referencing it will show a warning.</p>
                </div>
                <Button
                  label="Delete"
                  severity="danger"
                  outlined
                  @click="confirmDelete"
                />
              </div>
              <div class="danger-item" v-if="file.status === 'inactive'">
                <div class="danger-info">
                  <strong>Permanent Delete</strong>
                  <p>Permanently remove this file from storage. This cannot be undone.</p>
                </div>
                <Button
                  label="Delete Forever"
                  severity="danger"
                  @click="confirmHardDelete"
                />
              </div>
            </div>
          </template>
        </Card>
      </div>
    </template>
  </div>
</template>

<style scoped>
.file-detail-view {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1rem;
  padding: 3rem;
  color: var(--p-text-muted-color);
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 1rem;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.header-title {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.header-title h1 {
  margin: 0;
  font-size: 1.5rem;
  font-weight: 600;
}

.file-icon {
  font-size: 1.5rem;
  color: var(--p-primary-color);
}

.header-actions {
  display: flex;
  gap: 0.5rem;
}

.content-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1.5rem;
}

.info-card,
.metadata-card {
  grid-column: span 1;
}

.preview-card,
.danger-card,
.raw-json-panel,
.referencing-docs-card {
  grid-column: span 2;
}

.raw-json pre {
  background-color: var(--p-surface-100);
  padding: 1rem;
  border-radius: var(--p-border-radius);
  font-size: 0.75rem;
  overflow-x: auto;
  margin: 0;
  max-height: 600px;
}

.info-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1rem;
}

.info-item {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.info-item .label {
  font-size: 0.75rem;
  font-weight: 500;
  color: var(--p-text-muted-color);
  text-transform: uppercase;
}

.info-item .value {
  font-size: 0.875rem;
}

.info-item code.value {
  font-family: monospace;
  background-color: var(--p-surface-100);
  padding: 0.25rem 0.5rem;
  border-radius: 4px;
  font-size: 0.75rem;
  word-break: break-all;
}

.checksum {
  font-size: 0.7rem !important;
}

.no-refs {
  color: var(--p-orange-500);
}

.metadata-view {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.metadata-item {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.metadata-item .label {
  font-size: 0.75rem;
  font-weight: 500;
  color: var(--p-text-muted-color);
  text-transform: uppercase;
}

.metadata-item .value {
  font-size: 0.875rem;
}

.tags-list {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
}

.metadata-title {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.non-schema-badge {
  font-size: 0.625rem;
  font-weight: 400;
}

.metadata-edit {
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

.w-full {
  width: 100%;
}

.preview-container {
  display: flex;
  justify-content: center;
  padding: 1rem;
  background-color: var(--p-surface-100);
  border-radius: var(--p-border-radius);
}

.preview-image {
  max-width: 100%;
  max-height: 400px;
  object-fit: contain;
  border-radius: var(--p-border-radius);
}

.danger-card :deep(.p-card-title) {
  color: var(--p-red-600);
}

.danger-actions {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.danger-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 1rem;
  border: 1px solid var(--p-red-200);
  border-radius: var(--p-border-radius);
  background-color: var(--p-red-50);
}

.danger-info {
  flex: 1;
}

.danger-info strong {
  display: block;
  margin-bottom: 0.25rem;
}

.danger-info p {
  margin: 0;
  font-size: 0.875rem;
  color: var(--p-text-muted-color);
}

/* Referencing documents */
.referencing-docs-title {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.referencing-docs-card :deep(.p-card-content) {
  padding: 0;
}

.referencing-docs-table :deep(.p-datatable-tbody > tr:hover) {
  background-color: var(--p-surface-100);
}

.doc-id {
  font-family: monospace;
  font-size: 0.75rem;
  background-color: var(--p-surface-100);
  padding: 0.125rem 0.375rem;
  border-radius: 4px;
}

.field-path {
  font-size: 0.75rem;
  background-color: var(--p-surface-100);
  padding: 0.125rem 0.375rem;
  border-radius: 4px;
}

.date-value {
  font-size: 0.875rem;
  color: var(--p-text-muted-color);
}

.loading-inline {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 1rem;
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
}

.empty-state-inline {
  text-align: center;
  padding: 2rem;
  color: var(--p-text-muted-color);
}

@media (max-width: 768px) {
  .content-grid {
    grid-template-columns: 1fr;
  }

  .info-card,
  .metadata-card,
  .preview-card,
  .danger-card,
  .referencing-docs-card {
    grid-column: span 1;
  }

  .info-grid {
    grid-template-columns: 1fr;
  }
}
</style>
