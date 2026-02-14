<script setup lang="ts">
import { ref, onMounted, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useConfirm } from 'primevue/useconfirm'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'
import Tag from 'primevue/tag'
import Dialog from 'primevue/dialog'
import TruncatedId from '@/components/common/TruncatedId.vue'
import { useDocumentStore, useTemplateStore, useAuthStore, useUiStore } from '@/stores'
import type { Document } from '@/types'

const router = useRouter()
const confirm = useConfirm()
const documentStore = useDocumentStore()
const templateStore = useTemplateStore()
const authStore = useAuthStore()
const uiStore = useUiStore()

const searchQuery = ref('')
const statusFilter = ref<string | null>(null)
const templateFilter = ref<string | null>(null)

const statusOptions = [
  { label: 'All Status', value: null },
  { label: 'Active', value: 'active' },
  { label: 'Inactive', value: 'inactive' },
  { label: 'Archived', value: 'archived' }
]

// Create dialog
const showCreateDialog = ref(false)
const selectedTemplateId = ref<string | null>(null)

// Computed filtered documents
const filteredDocuments = computed(() => {
  let result = documentStore.documents

  if (searchQuery.value) {
    const query = searchQuery.value.toLowerCase()
    result = result.filter(
      d => d.document_id.toLowerCase().includes(query)
    )
  }

  return result
})

// Template options for filtering and creation
const templateOptions = computed(() => {
  const options: { label: string; value: string | null }[] = [{ label: 'All Templates', value: null }]

  templateStore.templates.forEach(t => {
    if (t.status === 'active') {
      options.push({
        label: `${t.label} (${t.value})`,
        value: t.template_id
      })
    }
  })

  return options
})

const createTemplateOptions = computed(() => {
  return templateStore.templates
    .filter(t => t.status === 'active')
    .map(t => ({
      label: `${t.label} (${t.value})`,
      value: t.template_id
    }))
})

// Get template name by ID
function getTemplateName(templateId: string): string {
  const template = templateStore.templates.find(t => t.template_id === templateId)
  return template ? template.label : templateId
}

async function loadDocuments() {
  if (!authStore.isAuthenticated) {
    return
  }

  try {
    await documentStore.fetchDocuments({
      status: statusFilter.value as 'active' | 'inactive' | 'archived' | undefined,
      template_id: templateFilter.value || undefined,
      page_size: 100
    })
  } catch (e) {
    uiStore.showError('Failed to load documents', e instanceof Error ? e.message : 'Unknown error')
  }
}

async function loadTemplates() {
  if (!authStore.isAuthenticated) {
    return
  }

  try {
    await templateStore.fetchTemplates({ status: 'active', page_size: 100 })
  } catch (e) {
    console.warn('Failed to load templates:', e)
  }
}

function openCreateDialog() {
  // Pre-select the currently filtered template if one is active
  selectedTemplateId.value = templateFilter.value || null
  showCreateDialog.value = true
}

function navigateToCreate() {
  if (!selectedTemplateId.value) {
    uiStore.showWarn('Validation Error', 'Please select a template')
    return
  }
  showCreateDialog.value = false
  router.push({
    name: 'document-create',
    query: { template: selectedTemplateId.value }
  })
}

function viewDocument(document: Document) {
  router.push(`/documents/${document.document_id}`)
}

function confirmDelete(document: Document) {
  confirm.require({
    message: `Are you sure you want to delete document "${document.document_id}"?`,
    header: 'Delete Document',
    icon: 'pi pi-exclamation-triangle',
    rejectLabel: 'Cancel',
    acceptLabel: 'Delete',
    acceptClass: 'p-button-danger',
    accept: async () => {
      try {
        await documentStore.deleteDocument(document.document_id)
        uiStore.showSuccess('Document Deleted', 'Document has been deleted')
      } catch (e) {
        uiStore.showError('Failed to delete', e instanceof Error ? e.message : 'Unknown error')
      }
    }
  })
}

function confirmArchive(document: Document) {
  confirm.require({
    message: `Are you sure you want to archive document "${document.document_id}"?`,
    header: 'Archive Document',
    icon: 'pi pi-inbox',
    rejectLabel: 'Cancel',
    acceptLabel: 'Archive',
    accept: async () => {
      try {
        await documentStore.archiveDocument(document.document_id)
        uiStore.showSuccess('Document Archived', 'Document has been archived')
      } catch (e) {
        uiStore.showError('Failed to archive', e instanceof Error ? e.message : 'Unknown error')
      }
    }
  })
}

function getStatusSeverity(status: string): "success" | "info" | "warn" | "danger" | "secondary" | "contrast" | undefined {
  switch (status) {
    case 'active':
      return 'info'
    case 'inactive':
      return 'warn'
    case 'archived':
      return 'secondary'
    default:
      return 'info'
  }
}

function formatDate(dateString: string): string {
  return new Date(dateString).toLocaleDateString()
}

function formatDateTime(dateString: string): string {
  return new Date(dateString).toLocaleString()
}

// Watch for filter changes
watch([statusFilter, templateFilter], () => {
  loadDocuments()
})

onMounted(async () => {
  await loadTemplates()
  await loadDocuments()
})
</script>

<template>
  <div class="document-list-view">
    <div class="page-header">
      <div class="header-left">
        <h1>Documents</h1>
        <span class="total-count">{{ documentStore.total }} documents</span>
      </div>
      <Button
        label="Create Document"
        icon="pi pi-plus"
        @click="openCreateDialog"
        :disabled="!authStore.isAuthenticated"
      />
    </div>

    <div v-if="!authStore.isAuthenticated" class="auth-warning">
      <i class="pi pi-exclamation-circle"></i>
      Please set your API key to access documents
    </div>

    <div v-else class="list-content">
      <div class="filters">
        <span class="p-input-icon-left search-input">
          <i class="pi pi-search" />
          <InputText
            v-model="searchQuery"
            placeholder="Search by document ID..."
            class="w-full"
          />
        </span>
        <Select
          v-model="templateFilter"
          :options="templateOptions"
          optionLabel="label"
          optionValue="value"
          placeholder="Template"
          class="filter-select"
        />
        <Select
          v-model="statusFilter"
          :options="statusOptions"
          optionLabel="label"
          optionValue="value"
          placeholder="Status"
          class="filter-select"
        />
        <Button
          icon="pi pi-refresh"
          severity="secondary"
          text
          rounded
          @click="loadDocuments"
          v-tooltip="'Refresh'"
        />
      </div>

      <DataTable
        :value="filteredDocuments"
        :loading="documentStore.loading"
        paginator
        :rows="20"
        :rowsPerPageOptions="[10, 20, 50]"
        stripedRows
        size="small"
        class="documents-table"
        @row-click="(e) => viewDocument(e.data)"
        rowHover
      >
        <Column field="document_id" header="Document ID" sortable style="width: 140px">
          <template #body="{ data }">
            <TruncatedId :id="data.document_id" />
          </template>
        </Column>
        <Column field="template_id" header="Template" sortable style="min-width: 200px">
          <template #body="{ data }">
            <span class="template-name">{{ getTemplateName(data.template_id) }}</span>
          </template>
        </Column>
        <Column field="version" header="Version" sortable style="width: 100px">
          <template #body="{ data }">
            <span class="version">v{{ data.version }}</span>
          </template>
        </Column>
        <Column field="status" header="Status" sortable style="width: 120px">
          <template #body="{ data }">
            <Tag :value="data.status" :severity="getStatusSeverity(data.status)" />
          </template>
        </Column>
        <Column field="created_at" header="Created" sortable style="width: 150px">
          <template #body="{ data }">
            <span v-tooltip="formatDateTime(data.created_at)">
              {{ formatDate(data.created_at) }}
            </span>
          </template>
        </Column>
        <Column field="updated_at" header="Updated" sortable style="width: 150px">
          <template #body="{ data }">
            <span v-tooltip="formatDateTime(data.updated_at)">
              {{ formatDate(data.updated_at) }}
            </span>
          </template>
        </Column>
        <Column header="Actions" style="width: 120px">
          <template #body="{ data }">
            <div class="actions" @click.stop>
              <Button
                icon="pi pi-pencil"
                severity="secondary"
                text
                rounded
                size="small"
                @click="viewDocument(data)"
                v-tooltip="'Edit'"
              />
              <Button
                v-if="data.status === 'active'"
                icon="pi pi-inbox"
                severity="secondary"
                text
                rounded
                size="small"
                @click="confirmArchive(data)"
                v-tooltip="'Archive'"
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

        <template #empty>
          <div class="empty-state">
            <i class="pi pi-folder-open"></i>
            <p>No documents found</p>
            <Button label="Create your first document" icon="pi pi-plus" @click="openCreateDialog" />
          </div>
        </template>
      </DataTable>
    </div>

    <!-- Create Document Dialog -->
    <Dialog
      v-model:visible="showCreateDialog"
      header="Create Document"
      :style="{ width: '500px' }"
      modal
    >
      <div class="create-form">
        <p class="help-text">
          Select a template to create a new document. The form will be generated based on the template's field definitions.
        </p>

        <div class="form-field">
          <label for="template">Template *</label>
          <Select
            id="template"
            v-model="selectedTemplateId"
            :options="createTemplateOptions"
            optionLabel="label"
            optionValue="value"
            placeholder="Select a template"
            class="w-full"
            filter
          />
        </div>
      </div>

      <template #footer>
        <Button label="Cancel" severity="secondary" text @click="showCreateDialog = false" />
        <Button
          label="Continue"
          icon="pi pi-arrow-right"
          @click="navigateToCreate"
          :disabled="!selectedTemplateId"
        />
      </template>
    </Dialog>
  </div>
</template>

<style scoped>
.document-list-view {
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
  align-items: baseline;
  gap: 1rem;
}

.page-header h1 {
  margin: 0;
  font-size: 1.75rem;
}

.total-count {
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

.filters {
  display: flex;
  gap: 0.75rem;
  flex-wrap: wrap;
  align-items: center;
}

.search-input {
  flex: 1;
  min-width: 200px;
  max-width: 400px;
}

.search-input .pi-search {
  left: 0.75rem;
}

.search-input input {
  padding-left: 2.5rem;
}

.filter-select {
  min-width: 180px;
}

.documents-table :deep(.p-datatable-tbody > tr) {
  cursor: pointer;
}

.document-id {
  background-color: var(--p-surface-100);
  padding: 0.25rem 0.5rem;
  border-radius: var(--p-border-radius);
  font-size: 0.75rem;
  word-break: break-all;
}

.template-name {
  font-weight: 500;
}

.version {
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
}

.actions {
  display: flex;
  gap: 0.25rem;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1rem;
  padding: 3rem;
  color: var(--p-text-muted-color);
}

.empty-state i {
  font-size: 3rem;
}

.create-form {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.help-text {
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
  line-height: 1.5;
  margin: 0;
}

.form-field {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.form-field label {
  font-weight: 500;
}

.w-full {
  width: 100%;
}
</style>
