<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useConfirm } from 'primevue/useconfirm'
import Card from 'primevue/card'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Textarea from 'primevue/textarea'
import Select from 'primevue/select'
import MultiSelect from 'primevue/multiselect'
import TabView from 'primevue/tabview'
import TabPanel from 'primevue/tabpanel'
import Tag from 'primevue/tag'
import Chips from 'primevue/chips'
import ToggleSwitch from 'primevue/toggleswitch'
import Message from 'primevue/message'
import FieldList from '@/components/templates/FieldList.vue'
import RuleList from '@/components/templates/RuleList.vue'
import TemplatePreview from '@/components/templates/TemplatePreview.vue'
import { useTemplateStore, useAuthStore, useUiStore } from '@/stores'
import type { Template, UpdateTemplateRequest, FieldDefinition, ValidationRule, SyncStrategy } from '@/types'
import { SYNC_STRATEGIES } from '@/types'
import InputNumber from 'primevue/inputnumber'

const props = defineProps<{
  id?: string
}>()

const router = useRouter()
const route = useRoute()
const confirm = useConfirm()
const templateStore = useTemplateStore()
const authStore = useAuthStore()
const uiStore = useUiStore()

const isNew = computed(() => !props.id && route.name === 'template-create')
const isEditing = ref(false)
const showRawView = ref(false)

// Form state
const form = ref<{
  code: string
  name: string
  description: string
  extends: string | null
  identity_fields: string[]
  fields: FieldDefinition[]
  rules: ValidationRule[]
  metadata: {
    domain: string
    category: string
    tags: string[]
  }
  reporting: {
    sync_enabled: boolean
    sync_strategy: SyncStrategy
    table_name: string
    include_metadata: boolean
    flatten_arrays: boolean
    max_array_elements: number
  }
}>({
  code: '',
  name: '',
  description: '',
  extends: null,
  identity_fields: [],
  fields: [],
  rules: [],
  metadata: {
    domain: '',
    category: '',
    tags: []
  },
  reporting: {
    sync_enabled: true,
    sync_strategy: 'latest_only',
    table_name: '',
    include_metadata: true,
    flatten_arrays: true,
    max_array_elements: 10
  }
})

// Template selection for extends dropdown
const templateOptions = computed(() => {
  const currentId = props.id
  return templateStore.templates
    .filter(t => t.template_id !== currentId && t.status === 'active')
    .map(t => ({
      label: `${t.name} (${t.code})`,
      value: t.template_id
    }))
})

// Field names for identity fields dropdown
const fieldNames = computed(() => form.value.fields.map(f => f.name))

// Current template being viewed
const template = computed(() =>
  showRawView.value ? templateStore.currentTemplateRaw : templateStore.currentTemplate
)

async function loadTemplate() {
  if (!authStore.isAuthenticated || isNew.value) {
    return
  }

  try {
    // Load all templates for the extends dropdown
    await templateStore.fetchTemplates({ page_size: 100 })

    // Load terminologies for field references
    await templateStore.fetchTerminologies()

    if (props.id) {
      await templateStore.fetchTemplateWithRaw(props.id)

      if (templateStore.currentTemplateRaw) {
        resetForm(templateStore.currentTemplateRaw)
      }
    }
  } catch (e) {
    uiStore.showError('Failed to load template', e instanceof Error ? e.message : 'Unknown error')
  }
}

function resetForm(t: Template) {
  form.value = {
    code: t.code,
    name: t.name,
    description: t.description || '',
    extends: t.extends || null,
    identity_fields: [...t.identity_fields],
    fields: JSON.parse(JSON.stringify(t.fields)),
    rules: JSON.parse(JSON.stringify(t.rules)),
    metadata: {
      domain: t.metadata.domain || '',
      category: t.metadata.category || '',
      tags: [...t.metadata.tags]
    },
    reporting: {
      sync_enabled: t.reporting?.sync_enabled ?? true,
      sync_strategy: t.reporting?.sync_strategy ?? 'latest_only',
      table_name: t.reporting?.table_name || '',
      include_metadata: t.reporting?.include_metadata ?? true,
      flatten_arrays: t.reporting?.flatten_arrays ?? true,
      max_array_elements: t.reporting?.max_array_elements ?? 10
    }
  }
}

async function saveTemplate() {
  if (!form.value.code || !form.value.name) {
    uiStore.showWarn('Validation Error', 'Code and Name are required')
    return
  }

  try {
    // Build reporting config (only include if changed from defaults or explicitly set)
    const reportingConfig = {
      sync_enabled: form.value.reporting.sync_enabled,
      sync_strategy: form.value.reporting.sync_strategy,
      table_name: form.value.reporting.table_name || undefined,
      include_metadata: form.value.reporting.include_metadata,
      flatten_arrays: form.value.reporting.flatten_arrays,
      max_array_elements: form.value.reporting.max_array_elements
    }

    if (isNew.value) {
      const created = await templateStore.createTemplate({
        code: form.value.code,
        name: form.value.name,
        description: form.value.description || undefined,
        extends: form.value.extends || undefined,
        identity_fields: form.value.identity_fields,
        fields: form.value.fields,
        rules: form.value.rules,
        metadata: {
          domain: form.value.metadata.domain || undefined,
          category: form.value.metadata.category || undefined,
          tags: form.value.metadata.tags,
          custom: {}
        },
        reporting: reportingConfig
      })
      uiStore.showSuccess('Template Created', `Template "${created.name}" has been created`)
      router.push(`/templates/${created.template_id}`)
    } else if (props.id) {
      const updateData: UpdateTemplateRequest = {
        code: form.value.code,
        name: form.value.name,
        description: form.value.description || undefined,
        extends: form.value.extends || undefined,
        identity_fields: form.value.identity_fields,
        fields: form.value.fields,
        rules: form.value.rules,
        metadata: {
          domain: form.value.metadata.domain || undefined,
          category: form.value.metadata.category || undefined,
          tags: form.value.metadata.tags,
          custom: {}
        },
        reporting: reportingConfig
      }
      await templateStore.updateTemplate(props.id, updateData)
      uiStore.showSuccess('Template Updated', `Template "${form.value.name}" has been saved`)
      isEditing.value = false

      // Reload to get updated resolved view
      await templateStore.fetchTemplateWithRaw(props.id)
      if (templateStore.currentTemplateRaw) {
        resetForm(templateStore.currentTemplateRaw)
      }
    }
  } catch (e) {
    uiStore.showError('Failed to save template', e instanceof Error ? e.message : 'Unknown error')
  }
}

function cancelEdit() {
  if (isNew.value) {
    router.push('/templates')
  } else if (templateStore.currentTemplateRaw) {
    resetForm(templateStore.currentTemplateRaw)
    isEditing.value = false
  }
}

function confirmDelete() {
  if (!template.value) return

  confirm.require({
    message: `Are you sure you want to delete "${template.value.name}"?`,
    header: 'Delete Template',
    icon: 'pi pi-exclamation-triangle',
    rejectLabel: 'Cancel',
    acceptLabel: 'Delete',
    acceptClass: 'p-button-danger',
    accept: async () => {
      try {
        await templateStore.deleteTemplate(template.value!.template_id)
        uiStore.showSuccess('Template Deleted', `Template has been deleted`)
        router.push('/templates')
      } catch (e) {
        uiStore.showError('Failed to delete', e instanceof Error ? e.message : 'Unknown error')
      }
    }
  })
}

async function validateTemplate() {
  if (!props.id) return

  try {
    const result = await templateStore.validateTemplate(props.id)
    if (result.valid) {
      uiStore.showSuccess('Validation Passed', 'All references are valid')
    } else {
      const errorMessages = result.errors.map(e => `${e.field}: ${e.message}`).join('\n')
      uiStore.showError('Validation Failed', errorMessages)
    }
  } catch (e) {
    uiStore.showError('Validation Error', e instanceof Error ? e.message : 'Unknown error')
  }
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
  return new Date(dateString).toLocaleString()
}

// Handle field updates
function onFieldsUpdate(fields: FieldDefinition[]) {
  form.value.fields = fields
}

// Handle rule updates
function onRulesUpdate(rules: ValidationRule[]) {
  form.value.rules = rules
}

// Watch for route changes (when navigating between templates)
watch(() => props.id, loadTemplate)

onMounted(async () => {
  if (!authStore.isAuthenticated) {
    return
  }

  // Load templates for dropdown
  await templateStore.fetchTemplates({ page_size: 100 })
  await templateStore.fetchTerminologies()

  if (isNew.value) {
    isEditing.value = true
    form.value = {
      code: '',
      name: '',
      description: '',
      extends: null,
      identity_fields: [],
      fields: [],
      rules: [],
      metadata: {
        domain: '',
        category: '',
        tags: []
      },
      reporting: {
        sync_enabled: true,
        sync_strategy: 'latest_only',
        table_name: '',
        include_metadata: true,
        flatten_arrays: true,
        max_array_elements: 10
      }
    }
  } else {
    await loadTemplate()
  }
})
</script>

<template>
  <div class="template-detail-view">
    <!-- Header -->
    <div class="page-header">
      <div class="header-left">
        <Button
          icon="pi pi-arrow-left"
          severity="secondary"
          text
          rounded
          @click="router.push('/templates')"
        />
        <div class="header-info" v-if="template && !isNew">
          <h1>{{ template.name }}</h1>
          <div class="header-meta">
            <code>{{ template.code }}</code>
            <Tag :value="template.status" :severity="getStatusSeverity(template.status)" />
            <span class="version">v{{ template.version }}</span>
          </div>
        </div>
        <h1 v-else>New Template</h1>
      </div>
      <div class="header-actions">
        <template v-if="isEditing || isNew">
          <Button label="Cancel" severity="secondary" text @click="cancelEdit" />
          <Button
            label="Save"
            icon="pi pi-check"
            @click="saveTemplate"
            :loading="templateStore.loading"
          />
        </template>
        <template v-else>
          <Button
            label="View as Table"
            icon="pi pi-table"
            severity="secondary"
            @click="router.push({ path: '/documents/table', query: { template: template?.template_id } })"
          />
          <Button
            label="Validate"
            icon="pi pi-check-circle"
            severity="secondary"
            @click="validateTemplate"
          />
          <Button
            label="Edit"
            icon="pi pi-pencil"
            @click="isEditing = true"
          />
          <Button
            icon="pi pi-trash"
            severity="danger"
            text
            rounded
            @click="confirmDelete"
          />
        </template>
      </div>
    </div>

    <div v-if="!authStore.isAuthenticated" class="auth-warning">
      <i class="pi pi-exclamation-circle"></i>
      Please set your API key to access templates
    </div>

    <div v-else-if="templateStore.loading && !template && !isNew" class="loading">
      <i class="pi pi-spin pi-spinner" style="font-size: 2rem"></i>
      <p>Loading template...</p>
    </div>

    <div v-else class="detail-content">
      <!-- Warning when no terminologies available -->
      <div v-if="templateStore.terminologies.length === 0 && (isEditing || isNew)" class="terminology-warning">
        <i class="pi pi-info-circle"></i>
        <div>
          <strong>No terminologies available</strong>
          <p>To create fields with controlled vocabulary (term type), you need to first define terminologies. Term fields require a terminology reference to ensure data consistency.</p>
        </div>
      </div>

      <!-- View Mode Toggle -->
      <div v-if="!isNew && template?.extends" class="view-toggle">
        <span>View:</span>
        <ToggleSwitch v-model="showRawView" />
        <span>{{ showRawView ? 'Raw (own fields only)' : 'Resolved (with inherited)' }}</span>
      </div>

      <!-- Basic Info Card -->
      <Card class="info-card">
        <template #title>Template Information</template>
        <template #content>
          <div class="form-grid">
            <div class="form-field">
              <label for="code">Code *</label>
              <InputText
                id="code"
                v-model="form.code"
                :disabled="!isEditing && !isNew"
                placeholder="e.g., PERSON"
                class="w-full"
              />
            </div>

            <div class="form-field">
              <label for="name">Name *</label>
              <InputText
                id="name"
                v-model="form.name"
                :disabled="!isEditing && !isNew"
                placeholder="e.g., Person Template"
                class="w-full"
              />
            </div>

            <div class="form-field full-width">
              <label for="description">Description</label>
              <Textarea
                id="description"
                v-model="form.description"
                :disabled="!isEditing && !isNew"
                placeholder="Describe the purpose of this template"
                rows="2"
                class="w-full"
              />
            </div>

            <div class="form-field">
              <label for="extends">Extends (Parent Template)</label>
              <Select
                id="extends"
                v-model="form.extends"
                :options="templateOptions"
                optionLabel="label"
                optionValue="value"
                :disabled="!isEditing && !isNew"
                placeholder="Select parent template"
                showClear
                class="w-full"
              />
              <small v-if="form.extends">This template inherits fields from the parent</small>
            </div>

            <div class="form-field">
              <label for="identity">Identity Fields</label>
              <MultiSelect
                id="identity"
                v-model="form.identity_fields"
                :options="fieldNames"
                :disabled="!isEditing && !isNew"
                placeholder="Select fields for document identity"
                display="chip"
                class="w-full"
              />
              <small v-if="form.identity_fields.length > 0" class="identity-hint">
                Documents will be identified by: {{ form.identity_fields.join(' + ') }}
              </small>
              <Message v-else severity="warn" :closable="false" class="identity-warning">
                No identity fields selected. Document versioning (upsert) will not work - each save creates a new document.
              </Message>
            </div>
          </div>
        </template>
      </Card>

      <!-- Metadata Card -->
      <Card class="metadata-card">
        <template #title>Metadata</template>
        <template #content>
          <div class="form-grid">
            <div class="form-field">
              <label for="domain">Domain</label>
              <InputText
                id="domain"
                v-model="form.metadata.domain"
                :disabled="!isEditing && !isNew"
                placeholder="e.g., hr, finance"
                class="w-full"
              />
            </div>

            <div class="form-field">
              <label for="category">Category</label>
              <InputText
                id="category"
                v-model="form.metadata.category"
                :disabled="!isEditing && !isNew"
                placeholder="e.g., master_data, transaction"
                class="w-full"
              />
            </div>

            <div class="form-field full-width">
              <label for="tags">Tags</label>
              <Chips
                id="tags"
                v-model="form.metadata.tags"
                :disabled="!isEditing && !isNew"
                placeholder="Add tags"
                class="w-full"
              />
            </div>
          </div>
        </template>
      </Card>

      <!-- Reporting Card -->
      <Card class="reporting-card">
        <template #title>
          <div class="card-title-with-toggle">
            <span>Reporting / Analytics</span>
            <div class="sync-toggle" v-if="isEditing || isNew">
              <span class="toggle-label">Sync to PostgreSQL</span>
              <ToggleSwitch v-model="form.reporting.sync_enabled" />
            </div>
            <Tag v-else :value="form.reporting.sync_enabled ? 'Enabled' : 'Disabled'"
                 :severity="form.reporting.sync_enabled ? 'success' : 'secondary'" />
          </div>
        </template>
        <template #content>
          <div v-if="form.reporting.sync_enabled" class="form-grid">
            <div class="form-field">
              <label for="sync_strategy">Sync Strategy</label>
              <Select
                id="sync_strategy"
                v-model="form.reporting.sync_strategy"
                :options="SYNC_STRATEGIES"
                optionLabel="label"
                optionValue="value"
                :disabled="!isEditing && !isNew"
                class="w-full"
              />
              <small>{{ SYNC_STRATEGIES.find(s => s.value === form.reporting.sync_strategy)?.description }}</small>
            </div>

            <div class="form-field">
              <label for="table_name">Custom Table Name</label>
              <InputText
                id="table_name"
                v-model="form.reporting.table_name"
                :disabled="!isEditing && !isNew"
                :placeholder="`doc_${form.code?.toLowerCase() || 'template'}`"
                class="w-full"
              />
              <small>Leave empty to auto-generate from template code</small>
            </div>

            <div class="form-field">
              <label>Include Metadata Columns</label>
              <div class="toggle-option">
                <ToggleSwitch
                  v-model="form.reporting.include_metadata"
                  :disabled="!isEditing && !isNew"
                />
                <span>{{ form.reporting.include_metadata ? 'Yes' : 'No' }}</span>
              </div>
              <small>Include created_at, created_by, updated_at, updated_by</small>
            </div>

            <div class="form-field">
              <label>Flatten Arrays</label>
              <div class="toggle-option">
                <ToggleSwitch
                  v-model="form.reporting.flatten_arrays"
                  :disabled="!isEditing && !isNew"
                />
                <span>{{ form.reporting.flatten_arrays ? 'Yes' : 'No' }}</span>
              </div>
              <small>Expand arrays into multiple rows (cross-product)</small>
            </div>

            <div class="form-field" v-if="form.reporting.flatten_arrays">
              <label for="max_array_elements">Max Array Elements</label>
              <InputNumber
                id="max_array_elements"
                v-model="form.reporting.max_array_elements"
                :disabled="!isEditing && !isNew"
                :min="1"
                :max="100"
                class="w-full"
              />
              <small>Limit array elements when flattening (1-100)</small>
            </div>
          </div>
          <div v-else class="sync-disabled-message">
            <i class="pi pi-info-circle"></i>
            <span>Documents of this template will not be synced to PostgreSQL for reporting.</span>
          </div>
        </template>
      </Card>

      <!-- Tabs for Fields, Rules, Preview -->
      <TabView>
        <TabPanel value="fields" header="Fields">
          <template #header>
            <span class="tab-header">
              <i class="pi pi-list"></i>
              Fields
              <Tag :value="form.fields.length.toString()" severity="secondary" rounded />
            </span>
          </template>
          <FieldList
            :fields="form.fields"
            :editable="isEditing || isNew"
            :terminologies="templateStore.terminologies"
            :templates="templateStore.templates"
            @update="onFieldsUpdate"
          />
        </TabPanel>

        <TabPanel value="rules" header="Rules">
          <template #header>
            <span class="tab-header">
              <i class="pi pi-check-square"></i>
              Rules
              <Tag :value="form.rules.length.toString()" severity="secondary" rounded />
            </span>
          </template>
          <RuleList
            :rules="form.rules"
            :fields="form.fields"
            :editable="isEditing || isNew"
            @update="onRulesUpdate"
          />
        </TabPanel>

        <TabPanel value="preview" header="Preview">
          <template #header>
            <span class="tab-header">
              <i class="pi pi-eye"></i>
              Preview
            </span>
          </template>
          <TemplatePreview
            :template="template"
            :resolved="!showRawView"
          />
        </TabPanel>
      </TabView>

      <!-- Audit Info -->
      <Card v-if="template && !isNew" class="audit-card">
        <template #content>
          <div class="audit-info">
            <span>Created: {{ formatDate(template.created_at) }}{{ template.created_by ? ` by ${template.created_by}` : '' }}</span>
            <span>Updated: {{ formatDate(template.updated_at) }}{{ template.updated_by ? ` by ${template.updated_by}` : '' }}</span>
          </div>
        </template>
      </Card>
    </div>
  </div>
</template>

<style scoped>
.template-detail-view {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 1rem;
}

.header-left {
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
}

.header-info h1 {
  margin: 0;
  font-size: 1.5rem;
}

.header-meta {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-top: 0.25rem;
}

.header-meta code {
  background-color: var(--p-surface-100);
  padding: 0.125rem 0.5rem;
  border-radius: var(--p-border-radius);
  font-size: 0.875rem;
}

.version {
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
}

.header-actions {
  display: flex;
  gap: 0.5rem;
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

.terminology-warning {
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
  padding: 1rem;
  background-color: var(--p-blue-50);
  border: 1px solid var(--p-blue-200);
  border-radius: var(--p-border-radius);
  color: var(--p-blue-700);
}

.terminology-warning i {
  font-size: 1.25rem;
  margin-top: 0.125rem;
}

.terminology-warning strong {
  display: block;
  margin-bottom: 0.25rem;
}

.terminology-warning p {
  margin: 0;
  font-size: 0.875rem;
}

.loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1rem;
  padding: 3rem;
  color: var(--p-text-muted-color);
}

.detail-content {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.view-toggle {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.75rem 1rem;
  background-color: var(--p-surface-50);
  border-radius: var(--p-border-radius);
  font-size: 0.875rem;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 1rem;
}

.form-field {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.form-field.full-width {
  grid-column: span 2;
}

.form-field label {
  font-weight: 500;
  font-size: 0.875rem;
}

.form-field small {
  color: var(--p-text-muted-color);
  font-size: 0.75rem;
}

.w-full {
  width: 100%;
}

.tab-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.audit-card :deep(.p-card-body) {
  padding: 0.75rem 1rem;
}

.audit-info {
  display: flex;
  gap: 2rem;
  font-size: 0.875rem;
  color: var(--p-text-muted-color);
}

@media (max-width: 768px) {
  .form-grid {
    grid-template-columns: 1fr;
  }

  .form-field.full-width {
    grid-column: span 1;
  }

  .audit-info {
    flex-direction: column;
    gap: 0.5rem;
  }
}

.identity-hint {
  color: var(--p-green-600);
  font-weight: 500;
}

.identity-warning {
  margin-top: 0.5rem;
}

.identity-warning :deep(.p-message-text) {
  font-size: 0.8125rem;
}

.card-title-with-toggle {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
}

.sync-toggle {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.875rem;
  font-weight: normal;
}

.toggle-label {
  color: var(--p-text-muted-color);
}

.toggle-option {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.sync-disabled-message {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.75rem;
  background-color: var(--p-surface-100);
  border-radius: var(--p-border-radius);
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
}

.sync-disabled-message i {
  font-size: 1rem;
}

.reporting-card :deep(.p-card-title) {
  width: 100%;
}
</style>
