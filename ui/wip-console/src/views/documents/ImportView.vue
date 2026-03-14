<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import Card from 'primevue/card'
import FileUpload from 'primevue/fileupload'
import Button from 'primevue/button'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Tag from 'primevue/tag'
import Select from 'primevue/select'
import Checkbox from 'primevue/checkbox'
import Message from 'primevue/message'
import { documentStoreClient, templateStoreClient } from '@/api/client'
import { useNamespaceStore } from '@/stores'

const router = useRouter()
const nsStore = useNamespaceStore()

const step = ref<'select-template' | 'upload' | 'mapping' | 'importing' | 'result'>('select-template')
const error = ref('')

// Step 1: Template selection
const templates = ref<Array<{ template_id: string; value: string; label: string }>>([])
const selectedTemplate = ref<{ template_id: string; value: string; label: string } | null>(null)
const templateFields = ref<Array<{ name: string; label: string; type: string; mandatory: boolean }>>([])
const loadingTemplates = ref(false)

// Step 2: File upload
const uploadedFile = ref<File | null>(null)
const previewData = ref<{
  format: string
  headers: string[]
  sample_rows: Record<string, string>[]
  total_rows: number
} | null>(null)
const parsing = ref(false)

// Step 3: Column mapping
const columnMapping = ref<Record<string, string>>({})
const skipErrors = ref(true)

// Step 4: Result
const importing = ref(false)
const importResult = ref<{
  total_rows: number
  succeeded: number
  failed: number
  skipped: number
  results: Array<{ row: number; document_id: string; version: number; is_new: boolean }>
  errors: Array<{ row: number; error: string; data?: Record<string, string> }>
} | null>(null)

// Load templates on mount
loadTemplates()

async function loadTemplates() {
  loadingTemplates.value = true
  try {
    const ns = nsStore.currentNamespaceParam
    const resp = await templateStoreClient.listTemplates({
      namespace: ns,
      status: 'active',
      latest_only: true,
      page_size: 100
    })
    templates.value = (resp.items || []).map((t: any) => ({
      template_id: t.template_id,
      value: t.value,
      label: t.label || t.value
    }))
  } catch (e: any) {
    error.value = `Failed to load templates: ${e.message}`
  } finally {
    loadingTemplates.value = false
  }
}

async function onTemplateSelected() {
  if (!selectedTemplate.value) return
  error.value = ''

  try {
    const tmpl = await templateStoreClient.getTemplate(selectedTemplate.value.template_id)
    templateFields.value = (tmpl.fields || []).map((f: any) => ({
      name: f.name,
      label: f.label || f.name,
      type: f.type || 'string',
      mandatory: f.mandatory || false
    }))
    step.value = 'upload'
  } catch (e: any) {
    error.value = `Failed to load template fields: ${e.message}`
  }
}

async function onFileSelect(event: any) {
  const files = event.files || []
  if (files.length === 0) return
  error.value = ''

  uploadedFile.value = files[0]
  parsing.value = true

  try {
    const result = await documentStoreClient.importPreview(files[0])
    if (result.error) {
      error.value = result.error
      return
    }
    previewData.value = result

    // Auto-map columns to template fields by name match
    const mapping: Record<string, string> = {}
    for (const header of result.headers) {
      const lower = header.toLowerCase().replace(/\s+/g, '_')
      const match = templateFields.value.find(
        f => f.name.toLowerCase() === lower || f.label.toLowerCase() === header.toLowerCase()
      )
      if (match) {
        mapping[header] = match.name
      }
    }
    columnMapping.value = mapping

    step.value = 'mapping'
  } catch (e: any) {
    error.value = `Failed to parse file: ${e.message}`
  } finally {
    parsing.value = false
  }
}

const unmappedMandatoryFields = computed(() => {
  const mappedFields = new Set(Object.values(columnMapping.value))
  return templateFields.value.filter(f => f.mandatory && !mappedFields.has(f.name))
})

const fieldOptions = computed(() => {
  return [
    { name: '', label: '-- Skip --' },
    ...templateFields.value
  ]
})

function setMapping(csvColumn: string, fieldName: string) {
  if (fieldName === '') {
    delete columnMapping.value[csvColumn]
  } else {
    columnMapping.value[csvColumn] = fieldName
  }
}

async function startImport() {
  if (!uploadedFile.value || !selectedTemplate.value) return
  error.value = ''

  // Filter out empty mappings
  const mapping: Record<string, string> = {}
  for (const [col, field] of Object.entries(columnMapping.value)) {
    if (field) mapping[col] = field
  }

  if (Object.keys(mapping).length === 0) {
    error.value = 'At least one column must be mapped to a template field'
    return
  }

  importing.value = true
  step.value = 'importing'

  try {
    const result = await documentStoreClient.importDocuments(
      uploadedFile.value,
      selectedTemplate.value.template_id,
      mapping,
      nsStore.currentNamespaceParam || 'wip',
      skipErrors.value
    )

    if (result.error) {
      error.value = result.error
      step.value = 'mapping'
    } else {
      importResult.value = result
      step.value = 'result'
    }
  } catch (e: any) {
    error.value = `Import failed: ${e.message}`
    step.value = 'mapping'
  } finally {
    importing.value = false
  }
}

function reset() {
  step.value = 'select-template'
  selectedTemplate.value = null
  uploadedFile.value = null
  previewData.value = null
  columnMapping.value = {}
  importResult.value = null
  error.value = ''
}
</script>

<template>
  <div class="import-view">
    <div class="page-header">
      <h1>Import Documents</h1>
      <Button label="Back to Documents" icon="pi pi-arrow-left" severity="secondary" text @click="router.push('/documents')" />
    </div>

    <Message v-if="error" severity="error" :closable="true" @close="error = ''">{{ error }}</Message>

    <!-- Step 1: Select Template -->
    <Card v-if="step === 'select-template'">
      <template #title>Step 1: Select Template</template>
      <template #content>
        <p>Choose the template that defines the structure for the documents you want to import.</p>
        <div class="field">
          <Select
            v-model="selectedTemplate"
            :options="templates"
            optionLabel="label"
            placeholder="Select a template"
            :loading="loadingTemplates"
            filter
            class="w-full"
          />
        </div>
        <Button
          label="Next"
          icon="pi pi-arrow-right"
          :disabled="!selectedTemplate"
          @click="onTemplateSelected"
          class="mt-3"
        />
      </template>
    </Card>

    <!-- Step 2: Upload File -->
    <Card v-if="step === 'upload'">
      <template #title>Step 2: Upload CSV or XLSX File</template>
      <template #subtitle>Template: {{ selectedTemplate?.label }}</template>
      <template #content>
        <p>Upload a CSV or XLSX file. The first row must contain column headers.</p>

        <div class="field-list mb-3">
          <strong>Template fields:</strong>
          <div class="flex flex-wrap gap-2 mt-2">
            <Tag v-for="f in templateFields" :key="f.name" :severity="f.mandatory ? 'danger' : 'info'">
              {{ f.name }} ({{ f.type }}){{ f.mandatory ? ' *' : '' }}
            </Tag>
          </div>
        </div>

        <FileUpload
          mode="basic"
          accept=".csv,.xlsx"
          :maxFileSize="50000000"
          :auto="true"
          chooseLabel="Choose File"
          customUpload
          @select="onFileSelect"
          :disabled="parsing"
        />
        <p v-if="parsing" class="mt-2"><i class="pi pi-spin pi-spinner"></i> Parsing file...</p>

        <div class="mt-3">
          <Button label="Back" icon="pi pi-arrow-left" severity="secondary" text @click="step = 'select-template'" />
        </div>
      </template>
    </Card>

    <!-- Step 3: Column Mapping -->
    <Card v-if="step === 'mapping' && previewData">
      <template #title>Step 3: Map Columns to Fields</template>
      <template #subtitle>
        {{ previewData.total_rows }} rows found ({{ previewData.format.toUpperCase() }})
      </template>
      <template #content>
        <Message v-if="unmappedMandatoryFields.length > 0" severity="warn" :closable="false">
          Unmapped mandatory fields: {{ unmappedMandatoryFields.map(f => f.name).join(', ') }}
        </Message>

        <DataTable :value="previewData.headers.map(h => ({ header: h }))" class="mb-4">
          <Column field="header" header="CSV Column" />
          <Column header="Maps To">
            <template #body="{ data }">
              <Select
                :modelValue="columnMapping[data.header] || ''"
                @update:modelValue="(v: string) => setMapping(data.header, v)"
                :options="fieldOptions"
                optionLabel="label"
                optionValue="name"
                placeholder="-- Skip --"
                class="w-full"
              />
            </template>
          </Column>
          <Column header="Sample Values">
            <template #body="{ data }">
              <span class="text-sm text-color-secondary">
                {{ previewData!.sample_rows.slice(0, 3).map(r => r[data.header]).filter(Boolean).join(', ') }}
              </span>
            </template>
          </Column>
        </DataTable>

        <div class="flex align-items-center gap-3 mb-3">
          <Checkbox v-model="skipErrors" :binary="true" inputId="skip-errors" />
          <label for="skip-errors">Skip rows with errors (import good rows anyway)</label>
        </div>

        <div class="flex gap-2">
          <Button label="Back" icon="pi pi-arrow-left" severity="secondary" text @click="step = 'upload'" />
          <Button
            label="Import"
            icon="pi pi-upload"
            :disabled="Object.keys(columnMapping).length === 0"
            @click="startImport"
          />
        </div>
      </template>
    </Card>

    <!-- Step 4: Importing -->
    <Card v-if="step === 'importing'">
      <template #title>Importing...</template>
      <template #content>
        <div class="flex align-items-center gap-3">
          <i class="pi pi-spin pi-spinner" style="font-size: 2rem"></i>
          <span>Importing {{ previewData?.total_rows }} documents...</span>
        </div>
      </template>
    </Card>

    <!-- Step 5: Results -->
    <Card v-if="step === 'result' && importResult">
      <template #title>Import Complete</template>
      <template #content>
        <div class="result-summary mb-4">
          <div class="flex gap-4">
            <div class="stat">
              <span class="stat-value text-green-500">{{ importResult.succeeded }}</span>
              <span class="stat-label">Succeeded</span>
            </div>
            <div class="stat">
              <span class="stat-value text-red-500">{{ importResult.failed }}</span>
              <span class="stat-label">Failed</span>
            </div>
            <div v-if="importResult.skipped > 0" class="stat">
              <span class="stat-value text-orange-500">{{ importResult.skipped }}</span>
              <span class="stat-label">Skipped</span>
            </div>
            <div class="stat">
              <span class="stat-value">{{ importResult.total_rows }}</span>
              <span class="stat-label">Total</span>
            </div>
          </div>
        </div>

        <div v-if="importResult.errors.length > 0" class="mb-4">
          <h3>Errors ({{ importResult.errors.length }})</h3>
          <DataTable :value="importResult.errors.slice(0, 50)" size="small">
            <Column field="row" header="Row" style="width: 80px" />
            <Column field="error" header="Error" />
          </DataTable>
        </div>

        <div class="flex gap-2">
          <Button label="Import Another" icon="pi pi-refresh" @click="reset" />
          <Button label="View Documents" icon="pi pi-list" severity="secondary" @click="router.push('/documents')" />
        </div>
      </template>
    </Card>
  </div>
</template>

<style scoped>
.import-view {
  max-width: 900px;
  margin: 0 auto;
  padding: 1rem;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1.5rem;
}

.page-header h1 {
  margin: 0;
}

.field {
  max-width: 400px;
}

.result-summary .stat {
  display: flex;
  flex-direction: column;
  align-items: center;
}

.result-summary .stat-value {
  font-size: 2rem;
  font-weight: bold;
}

.result-summary .stat-label {
  font-size: 0.875rem;
  color: var(--text-color-secondary);
}
</style>
