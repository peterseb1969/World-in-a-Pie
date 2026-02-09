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
// Dialog removed - template creation now uses /templates/new route
import ToggleSwitch from 'primevue/toggleswitch'
import Panel from 'primevue/panel'
import { useTemplateStore, useAuthStore, useUiStore, useNamespaceStore } from '@/stores'
import type { Template } from '@/types'

const router = useRouter()
const confirm = useConfirm()
const templateStore = useTemplateStore()
const authStore = useAuthStore()
const uiStore = useUiStore()
const namespaceStore = useNamespaceStore()

const searchQuery = ref('')
const statusFilter = ref<string | null>(null)
const extendsFilter = ref<string | null>(null)
const showAllVersions = ref(true)  // Show all versions by default (as per user requirement)
const wipCollapsed = ref(false)

const statusOptions = [
  { label: 'All Status', value: null },
  { label: 'Active', value: 'active' },
  { label: 'Deprecated (superseded)', value: 'deprecated' },
  { label: 'Inactive (deactivated)', value: 'inactive' }
]

// Create dialog removed — now navigates to /templates/new

// Get namespace prefix for display
const currentNamespacePrefix = computed(() => namespaceStore.current.toUpperCase())

// Computed filtered own templates
const filteredOwnTemplates = computed(() => {
  let result = templateStore.ownTemplates

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

// Computed filtered WIP templates
const filteredWipTemplates = computed(() => {
  let result = templateStore.wipTemplates

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

  templateStore.ownTemplates.forEach(t => {
    if (t.extends) {
      parents.add(t.extends)
    }
  })

  parents.forEach(p => {
    const parent = templateStore.ownTemplates.find(t => t.template_id === p)
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
      latest_only: !showAllVersions.value,
      page_size: 100
    })
  } catch (e) {
    uiStore.showError('Failed to load templates', e instanceof Error ? e.message : 'Unknown error')
  }
}

function createNewTemplate() {
  router.push('/templates/new')
}

function viewTemplate(template: Template) {
  router.push(`/templates/${template.template_id}`)
}

function confirmDeactivate(template: Template) {
  confirm.require({
    message: `Are you sure you want to deactivate "${template.name}"? It can be restored later.`,
    header: 'Deactivate Template',
    icon: 'pi pi-exclamation-triangle',
    rejectLabel: 'Cancel',
    acceptLabel: 'Deactivate',
    acceptClass: 'p-button-danger',
    accept: async () => {
      try {
        await templateStore.deleteTemplate(template.template_id)
        uiStore.showSuccess('Template Deactivated', `Template "${template.name}" has been deactivated`)
      } catch (e) {
        uiStore.showError('Deactivation Failed', e instanceof Error ? e.message : 'Unknown error')
      }
    }
  })
}

function getStatusSeverity(status: string): "success" | "info" | "warn" | "danger" | "secondary" | "contrast" | undefined {
  switch (status) {
    case 'active':
      return 'info'
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
        @click="createNewTemplate"
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
        <div class="versions-toggle">
          <ToggleSwitch v-model="showAllVersions" @change="loadTemplates" inputId="showAllVersions" />
          <label for="showAllVersions" class="versions-label">Show all versions</label>
        </div>
        <Button
          icon="pi pi-refresh"
          severity="secondary"
          text
          rounded
          @click="loadTemplates"
          v-tooltip="'Refresh'"
        />
      </div>

      <!-- Own Namespace Templates -->
      <div class="section-header">
        <Tag :value="currentNamespacePrefix" severity="info" />
        <span class="section-title">{{ currentNamespacePrefix }} Templates</span>
        <span class="section-count">({{ templateStore.total }})</span>
      </div>

      <DataTable
        :value="filteredOwnTemplates"
        :loading="templateStore.loading"
        paginator
        :rows="20"
        :rowsPerPageOptions="[10, 20, 50]"
        stripedRows
        size="small"
        class="templates-table"
        @row-click="(e) => viewTemplate(e.data)"
        rowHover
      >
        <Column field="code" header="Code" sortable style="width: 180px">
          <template #body="{ data }">
            <div class="template-code-cell">
              <code class="template-code">{{ data.code }}</code>
              <Tag v-if="showAllVersions" :value="`v${data.version}`" severity="info" class="version-tag" />
            </div>
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
        <Column field="fields" header="Fields" style="width: 80px">
          <template #body="{ data }">
            <span class="field-count">{{ data.fields.length }}</span>
          </template>
        </Column>
        <Column field="rules" header="Rules" style="width: 80px">
          <template #body="{ data }">
            <span class="rule-count">{{ data.rules.length }}</span>
          </template>
        </Column>
        <Column field="status" header="Status" sortable style="width: 100px">
          <template #body="{ data }">
            <Tag :value="data.status" :severity="getStatusSeverity(data.status)" />
          </template>
        </Column>
        <Column field="updated_at" header="Updated" sortable style="width: 100px">
          <template #body="{ data }">
            {{ formatDate(data.updated_at) }}
          </template>
        </Column>
        <Column header="Actions" style="width: 140px">
          <template #body="{ data }">
            <div class="actions" @click.stop>
              <Button
                icon="pi pi-table"
                severity="secondary"
                text
                rounded
                size="small"
                @click="router.push({ path: '/documents/table', query: { template: data.template_id } })"
                v-tooltip="'Browse Documents'"
              />
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
                icon="pi pi-ban"
                severity="danger"
                text
                rounded
                size="small"
                @click="confirmDeactivate(data)"
                v-tooltip="'Deactivate'"
              />
            </div>
          </template>
        </Column>

        <template #empty>
          <div class="empty-state">
            <i class="pi pi-file-edit"></i>
            <p>No templates in this namespace</p>
            <Button label="Create your first template" icon="pi pi-plus" @click="createNewTemplate" />
          </div>
        </template>
      </DataTable>

      <!-- WIP Templates (collapsible, read-only) -->
      <Panel
        v-if="templateStore.showWipSection"
        :collapsed="wipCollapsed"
        toggleable
        class="wip-section"
        @update:collapsed="wipCollapsed = $event"
      >
        <template #header>
          <div class="wip-header">
            <Tag value="WIP" severity="secondary" />
            <span class="section-title">WIP Templates (Read-only)</span>
            <span class="section-count">({{ templateStore.wipTotal }})</span>
          </div>
        </template>

        <DataTable
          :value="filteredWipTemplates"
          :loading="templateStore.loading"
          paginator
          :rows="10"
          :rowsPerPageOptions="[10, 20, 50]"
          stripedRows
          size="small"
          class="templates-table wip-table"
          @row-click="(e) => viewTemplate(e.data)"
          rowHover
        >
          <Column field="code" header="Code" sortable style="width: 180px">
            <template #body="{ data }">
              <div class="template-code-cell">
                <code class="template-code">{{ data.code }}</code>
                <Tag v-if="showAllVersions" :value="`v${data.version}`" severity="secondary" class="version-tag" />
              </div>
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
          <Column field="fields" header="Fields" style="width: 80px">
            <template #body="{ data }">
              <span class="field-count">{{ data.fields.length }}</span>
            </template>
          </Column>
          <Column field="rules" header="Rules" style="width: 80px">
            <template #body="{ data }">
              <span class="rule-count">{{ data.rules.length }}</span>
            </template>
          </Column>
          <Column field="status" header="Status" sortable style="width: 100px">
            <template #body="{ data }">
              <Tag :value="data.status" :severity="getStatusSeverity(data.status)" />
            </template>
          </Column>
          <Column header="Actions" style="width: 80px">
            <template #body="{ data }">
              <div class="actions" @click.stop>
                <Button
                  icon="pi pi-eye"
                  severity="secondary"
                  text
                  rounded
                  size="small"
                  @click="viewTemplate(data)"
                  v-tooltip="'View'"
                />
              </div>
            </template>
          </Column>

          <template #empty>
            <div class="empty-state small">
              <i class="pi pi-inbox"></i>
              <p>No WIP templates available</p>
            </div>
          </template>
        </DataTable>
      </Panel>
    </div>

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

.versions-toggle {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0 0.5rem;
}

.versions-label {
  font-size: 0.875rem;
  color: var(--p-text-muted-color);
  white-space: nowrap;
  cursor: pointer;
}

.section-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0;
  border-bottom: 1px solid var(--p-surface-200);
  margin-top: 0.5rem;
}

.section-title {
  font-weight: 600;
  color: var(--p-text-color);
}

.section-count {
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
}

.template-code-cell {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.version-tag {
  font-size: 0.7rem;
  padding: 0.1rem 0.4rem;
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

.empty-state.small {
  padding: 2rem;
}

.empty-state.small i {
  font-size: 2rem;
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

/* WIP Section styling */
.wip-section {
  margin-top: 1rem;
}

.wip-section :deep(.p-panel-header) {
  background: var(--p-surface-50);
  border-color: var(--p-surface-200);
}

.wip-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.wip-table {
  opacity: 0.9;
}

.wip-table :deep(.p-datatable-tbody > tr) {
  background: var(--p-surface-50);
}
</style>
