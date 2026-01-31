<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import Button from 'primevue/button'
import Card from 'primevue/card'
import TabView from 'primevue/tabview'
import TabPanel from 'primevue/tabpanel'
import Tag from 'primevue/tag'
import Select from 'primevue/select'
import Message from 'primevue/message'
import Dialog from 'primevue/dialog'
import { useDocumentStore, useTemplateStore, useAuthStore, useUiStore } from '@/stores'
import DocumentForm from '@/components/documents/DocumentForm.vue'
import VersionHistory from '@/components/documents/VersionHistory.vue'
import type { Template, DocumentValidationError, DocumentValidationResponse, Document } from '@/types'

const props = defineProps<{
  id?: string
}>()

const router = useRouter()
const route = useRoute()
const documentStore = useDocumentStore()
const templateStore = useTemplateStore()
const authStore = useAuthStore()
const uiStore = useUiStore()

// Mode: create or edit
const isCreateMode = computed(() => !props.id)

// Form data
const formData = ref<Record<string, unknown>>({})
const selectedTemplateId = ref<string | null>(null)
const selectedTemplate = ref<Template | null>(null)

// Validation state
const validationResult = ref<DocumentValidationResponse | null>(null)
const validationErrors = ref<DocumentValidationError[]>([])
const validationWarnings = ref<string[]>([])

// Version viewing
const viewingVersion = ref<Document | null>(null)
const showVersionDialog = ref(false)

// Active tab
const activeTab = ref(0)

// Template options for create mode
const templateOptions = computed(() => {
  return templateStore.templates
    .filter(t => t.status === 'active')
    .map(t => ({
      label: `${t.name} (${t.code})`,
      value: t.template_id
    }))
})

// Current template (either from document or selected)
const currentTemplate = computed(() => {
  if (isCreateMode.value) {
    return selectedTemplate.value
  }
  return documentStore.currentTemplate
})

// Page title
const pageTitle = computed(() => {
  if (isCreateMode.value) {
    return 'Create Document'
  }
  return `Document: ${props.id}`
})

// Load template when selection changes (create mode)
async function loadSelectedTemplate() {
  if (!selectedTemplateId.value) {
    selectedTemplate.value = null
    return
  }

  try {
    selectedTemplate.value = await templateStoreClient.getTemplate(selectedTemplateId.value)
    // Reset form data when template changes
    formData.value = {}
  } catch (e) {
    uiStore.showError('Failed to load template', e instanceof Error ? e.message : 'Unknown error')
  }
}

// Load document (edit mode)
async function loadDocument() {
  if (!props.id || !authStore.isAuthenticated) return

  try {
    await documentStore.fetchDocument(props.id)
    if (documentStore.currentDocument) {
      formData.value = { ...documentStore.currentDocument.data }
    }
  } catch (e) {
    uiStore.showError('Failed to load document', e instanceof Error ? e.message : 'Unknown error')
    router.push('/documents')
  }
}

// Load version history
async function loadVersionHistory() {
  if (!props.id) return

  try {
    await documentStore.fetchVersions(props.id)
  } catch (e) {
    console.warn('Failed to load version history:', e)
  }
}

// Validate document
async function validateDocument() {
  if (!currentTemplate.value) {
    uiStore.showWarn('Validation Error', 'No template selected')
    return
  }

  try {
    validationResult.value = await documentStore.validateDocument({
      template_id: currentTemplate.value.template_id,
      data: formData.value
    })

    validationErrors.value = validationResult.value.errors
    validationWarnings.value = validationResult.value.warnings

    if (validationResult.value.valid) {
      uiStore.showSuccess('Validation Passed', 'Document is valid')
    } else {
      uiStore.showWarn('Validation Failed', `${validationErrors.value.length} error(s) found`)
    }
  } catch (e) {
    uiStore.showError('Validation failed', e instanceof Error ? e.message : 'Unknown error')
  }
}

// Save document
async function saveDocument() {
  if (!currentTemplate.value) {
    uiStore.showWarn('Validation Error', 'No template selected')
    return
  }

  // Clear previous validation state
  validationErrors.value = []
  validationWarnings.value = []

  try {
    if (isCreateMode.value) {
      // Create new document
      const created = await documentStore.createDocument({
        template_id: currentTemplate.value.template_id,
        data: formData.value
      })
      uiStore.showSuccess('Document Created', 'Document has been created successfully')
      router.push(`/documents/${created.document_id}`)
    } else {
      // Update existing document (upsert - creates new version)
      const updated = await documentStore.updateDocument(
        currentTemplate.value.template_id,
        formData.value
      )
      uiStore.showSuccess('Document Updated', 'A new version has been created')
      // Navigate to the new document version if ID changed
      if (updated.document_id !== props.id) {
        router.push(`/documents/${updated.document_id}`)
      } else {
        // Reload version history
        await loadVersionHistory()
      }
    }
  } catch (e) {
    const errorMessage = e instanceof Error ? e.message : 'Unknown error'

    // Try to parse validation errors from the error message
    try {
      const errorData = JSON.parse(errorMessage)
      if (errorData.errors) {
        validationErrors.value = errorData.errors
        validationWarnings.value = errorData.warnings || []
        uiStore.showError('Validation Failed', `${validationErrors.value.length} error(s) found`)
        return
      }
    } catch {
      // Not a JSON error, show as-is
    }

    uiStore.showError('Failed to save document', errorMessage)
  }
}

// View a specific version
async function viewVersion(version: number) {
  if (!props.id) return

  try {
    viewingVersion.value = await documentStore.fetchVersion(props.id, version)
    showVersionDialog.value = true
  } catch (e) {
    uiStore.showError('Failed to load version', e instanceof Error ? e.message : 'Unknown error')
  }
}

// Restore version (copy data to form)
function restoreVersion() {
  if (!viewingVersion.value) return

  formData.value = { ...viewingVersion.value.data }
  showVersionDialog.value = false
  uiStore.showInfo('Version Restored', 'Data from the selected version has been loaded. Save to create a new version.')
}

// Cancel and go back
function cancel() {
  router.push('/documents')
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

function formatDateTime(dateString: string): string {
  return new Date(dateString).toLocaleString()
}

function getFieldValue(fieldPath: string): string {
  // Get the original value from document data for a field path
  const doc = documentStore.currentDocument
  if (!doc?.data) return '-'

  // Handle nested paths like "address.country"
  const parts = fieldPath.split('.')
  let value: unknown = doc.data
  for (const part of parts) {
    if (value && typeof value === 'object' && part in value) {
      value = (value as Record<string, unknown>)[part]
    } else {
      return '-'
    }
  }

  if (Array.isArray(value)) {
    return value.join(', ')
  }
  return String(value ?? '-')
}

// Import template store client for direct use
import { templateStoreClient } from '@/api/client'

// Watch for template selection in create mode
watch(selectedTemplateId, () => {
  if (isCreateMode.value) {
    loadSelectedTemplate()
  }
})

// Check for template query param in create mode
onMounted(async () => {
  if (!authStore.isAuthenticated) {
    uiStore.showWarn('Authentication Required', 'Please set your API key')
    router.push('/documents')
    return
  }

  // Load templates for the dropdown
  await templateStore.fetchTemplates({ status: 'active', page_size: 100 })

  if (isCreateMode.value) {
    // Check for template query param
    const templateParam = route.query.template as string
    if (templateParam) {
      selectedTemplateId.value = templateParam
      await loadSelectedTemplate()
    }
  } else {
    // Edit mode - load document
    await loadDocument()
    await loadVersionHistory()
  }
})
</script>

<template>
  <div class="document-detail-view">
    <div class="page-header">
      <div class="header-left">
        <Button
          icon="pi pi-arrow-left"
          severity="secondary"
          text
          rounded
          @click="cancel"
          v-tooltip="'Back to Documents'"
        />
        <h1>{{ pageTitle }}</h1>
        <Tag
          v-if="!isCreateMode && documentStore.currentDocument"
          :value="documentStore.currentDocument.status"
          :severity="getStatusSeverity(documentStore.currentDocument.status)"
        />
      </div>
      <div class="header-actions">
        <Button
          label="Validate"
          icon="pi pi-check-circle"
          severity="secondary"
          @click="validateDocument"
          :disabled="!currentTemplate"
          v-tooltip="'Validate document against template'"
        />
        <Button
          :label="isCreateMode ? 'Create' : 'Save'"
          icon="pi pi-save"
          @click="saveDocument"
          :loading="documentStore.loading"
          :disabled="!currentTemplate"
        />
      </div>
    </div>

    <!-- Document Info (Edit mode) -->
    <Card v-if="!isCreateMode && documentStore.currentDocument" class="document-info-card">
      <template #content>
        <div class="document-info">
          <div class="info-item">
            <span class="label">Document ID</span>
            <code>{{ documentStore.currentDocument.document_id }}</code>
          </div>
          <div class="info-item">
            <span class="label">Template</span>
            <span>{{ currentTemplate?.name }} ({{ currentTemplate?.code }})</span>
          </div>
          <div class="info-item">
            <span class="label">Version</span>
            <span>v{{ documentStore.currentDocument.version }}</span>
          </div>
          <div class="info-item">
            <span class="label">Template Version</span>
            <span>v{{ documentStore.currentDocument.template_version }}</span>
          </div>
          <div class="info-item">
            <span class="label">Identity Hash</span>
            <code class="hash">{{ documentStore.currentDocument.identity_hash }}</code>
          </div>
          <div class="info-item">
            <span class="label">Created</span>
            <span>{{ formatDateTime(documentStore.currentDocument.created_at) }}</span>
          </div>
          <div class="info-item">
            <span class="label">Updated</span>
            <span>{{ formatDateTime(documentStore.currentDocument.updated_at) }}</span>
          </div>
        </div>
      </template>
    </Card>

    <!-- Template Selection (Create mode) -->
    <Card v-if="isCreateMode" class="template-selection-card">
      <template #title>Select Template</template>
      <template #content>
        <div class="template-selection">
          <Select
            v-model="selectedTemplateId"
            :options="templateOptions"
            optionLabel="label"
            optionValue="value"
            placeholder="Select a template"
            filter
            class="template-select"
          />
          <p v-if="selectedTemplate" class="template-description">
            {{ selectedTemplate.description || 'No description available' }}
          </p>
        </div>
      </template>
    </Card>

    <!-- Validation Messages -->
    <div v-if="validationErrors.length > 0 || validationWarnings.length > 0" class="validation-messages">
      <Message v-for="err in validationErrors" :key="err.message" severity="error" :closable="false">
        <strong>{{ err.field || 'Document' }}:</strong> {{ err.message }}
        <span v-if="err.code" class="error-code">({{ err.code }})</span>
      </Message>
      <Message v-for="warn in validationWarnings" :key="warn" severity="warn" :closable="false">
        {{ warn }}
      </Message>
    </div>

    <!-- Main Content -->
    <Card v-if="currentTemplate" class="main-content-card">
      <template #content>
        <TabView v-model:activeIndex="activeTab">
          <TabPanel value="data" header="Data">
            <DocumentForm
              :template="currentTemplate"
              v-model="formData"
              :validationErrors="validationErrors"
            />
          </TabPanel>

          <TabPanel v-if="!isCreateMode" value="versions" header="Versions">
            <VersionHistory
              :versionHistory="documentStore.versionHistory"
              :currentVersion="documentStore.currentDocument?.version"
              :loading="documentStore.loading"
              @view-version="viewVersion"
            />
          </TabPanel>

          <TabPanel value="metadata" header="Metadata">
            <div class="metadata-section">
              <h4>Document Metadata</h4>
              <div v-if="!isCreateMode && documentStore.currentDocument?.metadata" class="metadata-content">
                <div class="metadata-item" v-if="documentStore.currentDocument.metadata.source_system">
                  <span class="label">Source System:</span>
                  <span>{{ documentStore.currentDocument.metadata.source_system }}</span>
                </div>
                <div class="metadata-item" v-if="documentStore.currentDocument.metadata.warnings?.length">
                  <span class="label">Warnings:</span>
                  <ul>
                    <li v-for="w in documentStore.currentDocument.metadata.warnings" :key="w">{{ w }}</li>
                  </ul>
                </div>
                <div class="metadata-item">
                  <span class="label">Custom:</span>
                  <pre>{{ JSON.stringify(documentStore.currentDocument.metadata.custom, null, 2) }}</pre>
                </div>
              </div>
              <div v-else class="no-metadata">
                <p>No metadata available</p>
              </div>

              <h4>Term References</h4>
              <div v-if="!isCreateMode && documentStore.currentDocument?.term_references && Object.keys(documentStore.currentDocument.term_references).length > 0" class="term-references">
                <p class="term-references-description">
                  Resolved term IDs for terminology fields. Original values are preserved in the data, these are the canonical term identifiers.
                </p>
                <table class="term-references-table">
                  <thead>
                    <tr>
                      <th>Field</th>
                      <th>Original Value</th>
                      <th>Term ID</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="(termId, field) in documentStore.currentDocument.term_references" :key="field">
                      <td><code>{{ field }}</code></td>
                      <td>{{ getFieldValue(field) }}</td>
                      <td>
                        <code v-if="Array.isArray(termId)">{{ termId.join(', ') }}</code>
                        <code v-else>{{ termId }}</code>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <div v-else-if="!isCreateMode" class="no-term-refs">
                <p>No term fields in this document</p>
              </div>

              <h4>Template Information</h4>
              <div class="template-info">
                <div class="metadata-item">
                  <span class="label">Code:</span>
                  <code>{{ currentTemplate.code }}</code>
                </div>
                <div class="metadata-item">
                  <span class="label">Version:</span>
                  <span>v{{ currentTemplate.version }}</span>
                </div>
                <div class="metadata-item">
                  <span class="label">Fields:</span>
                  <span>{{ currentTemplate.fields.length }}</span>
                </div>
                <div class="metadata-item">
                  <span class="label">Rules:</span>
                  <span>{{ currentTemplate.rules.length }}</span>
                </div>
                <div class="metadata-item">
                  <span class="label">Identity Fields:</span>
                  <span>{{ currentTemplate.identity_fields.join(', ') || 'None' }}</span>
                </div>
                <div class="template-actions">
                  <router-link :to="{ path: '/templates/' + selectedTemplate }" class="template-link">
                    <i class="pi pi-external-link"></i> View Template
                  </router-link>
                  <router-link :to="{ path: '/documents/table', query: { template: selectedTemplateId } }" class="template-link">
                    <i class="pi pi-table"></i> View as Table
                  </router-link>
                </div>
              </div>
            </div>
          </TabPanel>

          <TabPanel value="raw" header="Raw JSON">
            <div class="raw-json">
              <template v-if="!isCreateMode && documentStore.currentDocument">
                <h4>API Response (Document)</h4>
                <pre>{{ JSON.stringify(documentStore.currentDocument, null, 2) }}</pre>
              </template>
              <template v-else>
                <h4>Current Form Data</h4>
                <pre>{{ JSON.stringify(formData, null, 2) }}</pre>
              </template>
            </div>
          </TabPanel>
        </TabView>
      </template>
    </Card>

    <!-- No template selected message -->
    <Card v-else-if="isCreateMode" class="no-template-card">
      <template #content>
        <div class="no-template">
          <i class="pi pi-file-edit"></i>
          <p>Please select a template to start creating a document</p>
        </div>
      </template>
    </Card>

    <!-- Version View Dialog -->
    <Dialog
      v-model:visible="showVersionDialog"
      :header="`Version ${viewingVersion?.version}`"
      :style="{ width: '700px' }"
      modal
    >
      <div v-if="viewingVersion" class="version-dialog-content">
        <div class="version-info">
          <div class="info-row">
            <span class="label">Status:</span>
            <Tag :value="viewingVersion.status" :severity="getStatusSeverity(viewingVersion.status)" />
          </div>
          <div class="info-row">
            <span class="label">Created:</span>
            <span>{{ formatDateTime(viewingVersion.created_at) }}</span>
          </div>
          <div class="info-row" v-if="viewingVersion.created_by">
            <span class="label">Created By:</span>
            <span>{{ viewingVersion.created_by }}</span>
          </div>
        </div>

        <h4>Data</h4>
        <pre class="version-data">{{ JSON.stringify(viewingVersion.data, null, 2) }}</pre>
      </div>

      <template #footer>
        <Button
          label="Close"
          severity="secondary"
          text
          @click="showVersionDialog = false"
        />
        <Button
          label="Restore This Version"
          icon="pi pi-refresh"
          @click="restoreVersion"
          v-tooltip="'Copy this version\'s data to the form'"
        />
      </template>
    </Dialog>
  </div>
</template>

<style scoped>
.document-detail-view {
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
  gap: 0.75rem;
}

.header-left h1 {
  margin: 0;
  font-size: 1.5rem;
}

.header-actions {
  display: flex;
  gap: 0.5rem;
}

.document-info-card :deep(.p-card-content) {
  padding-top: 0;
}

.document-info {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 1rem;
}

.info-item {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.info-item .label {
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.info-item code {
  background-color: var(--p-surface-100);
  padding: 0.25rem 0.5rem;
  border-radius: var(--p-border-radius);
  font-size: 0.75rem;
  word-break: break-all;
}

.info-item code.hash {
  font-size: 0.625rem;
}

.template-selection-card :deep(.p-card-content) {
  padding-top: 0;
}

.template-selection {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.template-select {
  max-width: 400px;
}

.template-description {
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
  margin: 0;
}

.validation-messages {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.error-code {
  font-family: monospace;
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
}

.main-content-card :deep(.p-card-content) {
  padding: 0;
}

.main-content-card :deep(.p-tabview-panels) {
  padding: 1.5rem;
}

.metadata-section {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.metadata-section h4 {
  margin: 0;
  color: var(--p-text-color);
  border-bottom: 1px solid var(--p-surface-200);
  padding-bottom: 0.5rem;
}

.metadata-content,
.template-info {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.metadata-item {
  display: flex;
  gap: 0.5rem;
  font-size: 0.875rem;
}

.template-actions {
  display: flex;
  gap: 1rem;
  margin-top: 0.75rem;
  padding-top: 0.75rem;
  border-top: 1px solid var(--p-surface-200);
}

.template-link {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  color: var(--p-primary-color);
  text-decoration: none;
  font-size: 0.875rem;
}

.template-link:hover {
  text-decoration: underline;
}

.template-link i {
  font-size: 0.75rem;
}

.metadata-item .label {
  color: var(--p-text-muted-color);
  min-width: 120px;
}

.metadata-item pre {
  background-color: var(--p-surface-100);
  padding: 0.5rem;
  border-radius: var(--p-border-radius);
  font-size: 0.75rem;
  overflow-x: auto;
  margin: 0;
}

.metadata-item ul {
  margin: 0;
  padding-left: 1.5rem;
}

.no-metadata,
.no-term-refs {
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
}

.term-references {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.term-references-description {
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
  margin: 0;
}

.term-references-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.875rem;
}

.term-references-table th,
.term-references-table td {
  text-align: left;
  padding: 0.5rem;
  border-bottom: 1px solid var(--p-surface-200);
}

.term-references-table th {
  font-weight: 600;
  color: var(--p-text-muted-color);
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.term-references-table code {
  background-color: var(--p-surface-100);
  padding: 0.125rem 0.375rem;
  border-radius: var(--p-border-radius);
  font-size: 0.75rem;
}

.raw-json {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.raw-json h4 {
  margin: 0;
}

.raw-json pre {
  background-color: var(--p-surface-100);
  padding: 1rem;
  border-radius: var(--p-border-radius);
  font-size: 0.75rem;
  overflow-x: auto;
  margin: 0;
}

.no-template-card :deep(.p-card-content) {
  padding: 3rem;
}

.no-template {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1rem;
  color: var(--p-text-muted-color);
}

.no-template i {
  font-size: 3rem;
}

.no-template p {
  margin: 0;
}

.version-dialog-content {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.version-info {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  padding: 0.75rem 1rem;
  background-color: var(--p-surface-50);
  border-radius: var(--p-border-radius);
}

.info-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.875rem;
}

.info-row .label {
  color: var(--p-text-muted-color);
  min-width: 100px;
}

.version-dialog-content h4 {
  margin: 0;
}

.version-data {
  background-color: var(--p-surface-100);
  padding: 1rem;
  border-radius: var(--p-border-radius);
  font-size: 0.75rem;
  overflow-x: auto;
  max-height: 300px;
  margin: 0;
}
</style>
