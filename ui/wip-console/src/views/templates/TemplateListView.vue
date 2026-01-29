<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { useConfirm } from 'primevue/useconfirm'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'
import Tag from 'primevue/tag'
import Dialog from 'primevue/dialog'
import { useTemplateStore, useAuthStore, useUiStore } from '@/stores'
import type { Template, CreateTemplateRequest } from '@/types'

const router = useRouter()
const confirm = useConfirm()
const templateStore = useTemplateStore()
const authStore = useAuthStore()
const uiStore = useUiStore()

const searchQuery = ref('')
const statusFilter = ref<string | null>(null)
const extendsFilter = ref<string | null>(null)

const statusOptions = [
  { label: 'All Status', value: null },
  { label: 'Active', value: 'active' },
  { label: 'Deprecated', value: 'deprecated' },
  { label: 'Inactive', value: 'inactive' }
]

// Create dialog
const showCreateDialog = ref(false)
const createForm = ref<CreateTemplateRequest>({
  code: '',
  name: '',
  description: ''
})

// Computed filtered templates
const filteredTemplates = computed(() => {
  let result = templateStore.templates

  if (searchQuery.value) {
    const query = searchQuery.value.toLowerCase()
    result = result.filter(
      t =>
        t.name.toLowerCase().includes(query) ||
        t.code.toLowerCase().includes(query) ||
        t.description?.toLowerCase().includes(query)
    )
  }

  return result
})

// Extends options for filtering
const extendsOptions = computed(() => {
  const options: { label: string; value: string | null }[] = [{ label: 'All Parents', value: null }]
  const parents = new Set<string>()

  templateStore.templates.forEach(t => {
    if (t.extends) {
      parents.add(t.extends)
    }
  })

  parents.forEach(p => {
    const parent = templateStore.templates.find(t => t.template_id === p)
    options.push({
      label: parent ? parent.name : p,
      value: p
    })
  })

  return options
})

async function loadTemplates() {
  if (!authStore.isAuthenticated) {
    return
  }

  try {
    await templateStore.fetchTemplates({
      status: statusFilter.value || undefined,
      extends: extendsFilter.value || undefined,
      page_size: 100
    })
  } catch (e) {
    uiStore.showError('Failed to load templates', e instanceof Error ? e.message : 'Unknown error')
  }
}

function openCreateDialog() {
  createForm.value = {
    code: '',
    name: '',
    description: ''
  }
  showCreateDialog.value = true
}

async function createTemplate() {
  if (!createForm.value.code || !createForm.value.name) {
    uiStore.showWarn('Validation Error', 'Code and Name are required')
    return
  }

  try {
    const created = await templateStore.createTemplate(createForm.value)
    showCreateDialog.value = false
    uiStore.showSuccess('Template Created', `Template "${created.name}" has been created`)
    router.push(`/templates/${created.template_id}`)
  } catch (e) {
    uiStore.showError('Failed to create template', e instanceof Error ? e.message : 'Unknown error')
  }
}

function viewTemplate(template: Template) {
  router.push(`/templates/${template.template_id}`)
}

function confirmDelete(template: Template) {
  confirm.require({
    message: `Are you sure you want to delete "${template.name}"?`,
    header: 'Delete Template',
    icon: 'pi pi-exclamation-triangle',
    rejectLabel: 'Cancel',
    acceptLabel: 'Delete',
    acceptClass: 'p-button-danger',
    accept: async () => {
      try {
        await templateStore.deleteTemplate(template.template_id)
        uiStore.showSuccess('Template Deleted', `Template "${template.name}" has been deleted`)
      } catch (e) {
        uiStore.showError('Failed to delete', e instanceof Error ? e.message : 'Unknown error')
      }
    }
  })
}

function getStatusSeverity(status: string): "success" | "info" | "warn" | "danger" | "secondary" | "contrast" | undefined {
  switch (status) {
    case 'active':
      return 'success'
    case 'deprecated':
      return 'warn'
    case 'inactive':
      return 'secondary'
    default:
      return 'info'
  }
}

function formatDate(dateString: string): string {
  return new Date(dateString).toLocaleDateString()
}

onMounted(loadTemplates)
</script>

<template>
  <div class="template-list-view">
    <div class="page-header">
      <div class="header-left">
        <h1>Templates</h1>
        <span class="total-count">{{ templateStore.total }} templates</span>
      </div>
      <Button
        label="Create Template"
        icon="pi pi-plus"
        @click="openCreateDialog"
        :disabled="!authStore.isAuthenticated"
      />
    </div>

    <div v-if="!authStore.isAuthenticated" class="auth-warning">
      <i class="pi pi-exclamation-circle"></i>
      Please set your API key to access templates
    </div>

    <div v-else class="list-content">
      <div class="filters">
        <span class="p-input-icon-left search-input">
          <i class="pi pi-search" />
          <InputText
            v-model="searchQuery"
            placeholder="Search templates..."
            class="w-full"
          />
        </span>
        <Select
          v-model="statusFilter"
          :options="statusOptions"
          optionLabel="label"
          optionValue="value"
          placeholder="Status"
          class="filter-select"
          @change="loadTemplates"
        />
        <Select
          v-model="extendsFilter"
          :options="extendsOptions"
          optionLabel="label"
          optionValue="value"
          placeholder="Parent"
          class="filter-select"
          @change="loadTemplates"
        />
        <Button
          icon="pi pi-refresh"
          severity="secondary"
          text
          rounded
          @click="loadTemplates"
          v-tooltip="'Refresh'"
        />
      </div>

      <DataTable
        :value="filteredTemplates"
        :loading="templateStore.loading"
        paginator
        :rows="20"
        :rowsPerPageOptions="[10, 20, 50]"
        stripedRows
        class="templates-table"
        @row-click="(e) => viewTemplate(e.data)"
        rowHover
      >
        <Column field="code" header="Code" sortable style="width: 150px">
          <template #body="{ data }">
            <code class="template-code">{{ data.code }}</code>
          </template>
        </Column>
        <Column field="name" header="Name" sortable style="min-width: 200px">
          <template #body="{ data }">
            <div class="template-name-cell">
              <span class="name">{{ data.name }}</span>
              <span v-if="data.extends" class="extends-badge">
                <i class="pi pi-arrow-right"></i>
                {{ data.extends }}
              </span>
            </div>
          </template>
        </Column>
        <Column field="fields" header="Fields" style="width: 100px">
          <template #body="{ data }">
            <span class="field-count">{{ data.fields.length }}</span>
          </template>
        </Column>
        <Column field="rules" header="Rules" style="width: 100px">
          <template #body="{ data }">
            <span class="rule-count">{{ data.rules.length }}</span>
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
        <Column field="updated_at" header="Updated" sortable style="width: 120px">
          <template #body="{ data }">
            {{ formatDate(data.updated_at) }}
          </template>
        </Column>
        <Column header="Actions" style="width: 100px">
          <template #body="{ data }">
            <div class="actions" @click.stop>
              <Button
                icon="pi pi-pencil"
                severity="secondary"
                text
                rounded
                size="small"
                @click="viewTemplate(data)"
                v-tooltip="'Edit'"
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
            <i class="pi pi-file-edit"></i>
            <p>No templates found</p>
            <Button label="Create your first template" icon="pi pi-plus" @click="openCreateDialog" />
          </div>
        </template>
      </DataTable>
    </div>

    <!-- Create Template Dialog -->
    <Dialog
      v-model:visible="showCreateDialog"
      header="Create Template"
      :style="{ width: '500px' }"
      modal
    >
      <div class="create-form">
        <div class="form-field">
          <label for="code">Code *</label>
          <InputText
            id="code"
            v-model="createForm.code"
            placeholder="e.g., PERSON, ADDRESS"
            class="w-full"
          />
          <small>Unique identifier for the template</small>
        </div>

        <div class="form-field">
          <label for="name">Name *</label>
          <InputText
            id="name"
            v-model="createForm.name"
            placeholder="e.g., Person Template"
            class="w-full"
          />
        </div>

        <div class="form-field">
          <label for="description">Description</label>
          <InputText
            id="description"
            v-model="createForm.description"
            placeholder="Brief description of the template"
            class="w-full"
          />
        </div>
      </div>

      <template #footer>
        <Button label="Cancel" severity="secondary" text @click="showCreateDialog = false" />
        <Button
          label="Create"
          icon="pi pi-plus"
          @click="createTemplate"
          :loading="templateStore.loading"
        />
      </template>
    </Dialog>
  </div>
</template>

<style scoped>
.template-list-view {
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
  min-width: 150px;
}

.templates-table :deep(.p-datatable-tbody > tr) {
  cursor: pointer;
}

.template-code {
  background-color: var(--p-surface-100);
  padding: 0.25rem 0.5rem;
  border-radius: var(--p-border-radius);
  font-size: 0.875rem;
}

.template-name-cell {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.template-name-cell .name {
  font-weight: 500;
}

.extends-badge {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
}

.extends-badge i {
  font-size: 0.625rem;
}

.field-count,
.rule-count,
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

.form-field {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.form-field label {
  font-weight: 500;
}

.form-field small {
  color: var(--p-text-muted-color);
}

.w-full {
  width: 100%;
}
</style>
