<script setup lang="ts">
import { ref, watch } from 'vue'
import Dialog from 'primevue/dialog'
import FileUpload from 'primevue/fileupload'
import Textarea from 'primevue/textarea'
import Button from 'primevue/button'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Tag from 'primevue/tag'
import TabView from 'primevue/tabview'
import TabPanel from 'primevue/tabpanel'
import { useTermStore, useUiStore } from '@/stores'
import type { CreateTermRequest, BulkOperationResponse } from '@/types'

const props = defineProps<{
  visible: boolean
  terminologyId: string
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
}>()

const termStore = useTermStore()
const uiStore = useUiStore()

const step = ref<'input' | 'preview' | 'result'>('input')
const activeTab = ref(0)
const jsonInput = ref('')
const csvInput = ref('')
const parsedTerms = ref<CreateTermRequest[]>([])
const importing = ref(false)
const importResult = ref<BulkOperationResponse | null>(null)

watch(
  () => props.visible,
  (visible) => {
    if (visible) {
      reset()
    }
  }
)

function reset() {
  step.value = 'input'
  jsonInput.value = ''
  csvInput.value = ''
  parsedTerms.value = []
  importResult.value = null
}

function parseJSON() {
  try {
    const data = JSON.parse(jsonInput.value)
    const terms = Array.isArray(data) ? data : data.terms || [data]
    parsedTerms.value = terms.map((t: CreateTermRequest, i: number) => ({
      code: t.code || '',
      value: t.value || '',
      label: t.label || '',
      description: t.description,
      sort_order: t.sort_order ?? i
    }))
    step.value = 'preview'
  } catch (e) {
    uiStore.showError('Parse Error', 'Invalid JSON format')
  }
}

function parseCSV() {
  const lines = csvInput.value.split('\n').filter(l => l.trim())
  if (lines.length < 2) {
    uiStore.showError('Parse Error', 'CSV must have a header row and at least one data row')
    return
  }

  const headers = lines[0].split(',').map(h => h.trim().toLowerCase())
  const requiredHeaders = ['code', 'value', 'label']
  const missingHeaders = requiredHeaders.filter(h => !headers.includes(h))

  if (missingHeaders.length > 0) {
    uiStore.showError('Parse Error', `CSV missing required columns: ${missingHeaders.join(', ')}`)
    return
  }

  const terms: CreateTermRequest[] = []
  for (let i = 1; i < lines.length; i++) {
    const values = lines[i].split(',').map(v => v.trim())
    const term: CreateTermRequest = {
      code: values[headers.indexOf('code')] || '',
      value: values[headers.indexOf('value')] || '',
      label: values[headers.indexOf('label')] || '',
      description: headers.includes('description') ? values[headers.indexOf('description')] : undefined,
      sort_order: headers.includes('sort_order') ? parseInt(values[headers.indexOf('sort_order')]) : i - 1
    }
    if (term.code && term.value && term.label) {
      terms.push(term)
    }
  }

  parsedTerms.value = terms
  step.value = 'preview'
}

async function onFileSelect(event: { files: File[] }) {
  const file = event.files[0]
  if (!file) return

  const text = await file.text()

  if (file.name.endsWith('.json')) {
    jsonInput.value = text
    activeTab.value = 0
    parseJSON()
  } else if (file.name.endsWith('.csv')) {
    csvInput.value = text
    activeTab.value = 1
    parseCSV()
  }
}

async function doImport() {
  if (parsedTerms.value.length === 0) return

  importing.value = true
  try {
    importResult.value = await termStore.bulkCreateTerms(props.terminologyId, {
      terms: parsedTerms.value
    })
    step.value = 'result'
    uiStore.showSuccess('Import Complete', `Created ${importResult.value.succeeded} terms`)
  } catch (e) {
    uiStore.showError('Import Failed', (e as Error).message)
  } finally {
    importing.value = false
  }
}

function close() {
  emit('update:visible', false)
}

function getResultSeverity(status: string): 'success' | 'warn' | 'danger' | 'info' | 'secondary' | 'contrast' | undefined {
  switch (status) {
    case 'created': return 'success'
    case 'updated': return 'info'
    case 'skipped': return 'warn'
    case 'error': return 'danger'
    default: return 'secondary'
  }
}
</script>

<template>
  <Dialog
    :visible="visible"
    @update:visible="$emit('update:visible', $event)"
    header="Bulk Import Terms"
    :style="{ width: '700px' }"
    modal
  >
    <!-- Step 1: Input -->
    <div v-if="step === 'input'" class="input-step">
      <div class="file-upload-section">
        <FileUpload
          mode="basic"
          accept=".json,.csv"
          :auto="true"
          choose-label="Upload File"
          custom-upload
          @select="onFileSelect"
        />
        <span class="or-divider">or paste data below</span>
      </div>

      <TabView v-model:value="activeTab">
        <TabPanel value="0" header="JSON">
          <Textarea
            v-model="jsonInput"
            rows="10"
            placeholder='[
  { "code": "ACTIVE", "value": "active", "label": "Active" },
  { "code": "INACTIVE", "value": "inactive", "label": "Inactive" }
]'
            class="input-textarea"
          />
          <Button
            label="Parse JSON"
            icon="pi pi-check"
            class="parse-btn"
            @click="parseJSON"
            :disabled="!jsonInput.trim()"
          />
        </TabPanel>

        <TabPanel value="1" header="CSV">
          <Textarea
            v-model="csvInput"
            rows="10"
            placeholder="code,value,label,description
ACTIVE,active,Active,Currently active
INACTIVE,inactive,Inactive,Not active"
            class="input-textarea"
          />
          <Button
            label="Parse CSV"
            icon="pi pi-check"
            class="parse-btn"
            @click="parseCSV"
            :disabled="!csvInput.trim()"
          />
        </TabPanel>
      </TabView>
    </div>

    <!-- Step 2: Preview -->
    <div v-if="step === 'preview'" class="preview-step">
      <div class="preview-header">
        <span>{{ parsedTerms.length }} terms to import</span>
        <Button
          label="Back"
          severity="secondary"
          text
          size="small"
          @click="step = 'input'"
        />
      </div>

      <DataTable :value="parsedTerms" striped-rows size="small" scrollable scroll-height="300px">
        <Column field="code" header="Code" style="width: 20%" />
        <Column field="value" header="Value" style="width: 20%" />
        <Column field="label" header="Label" style="width: 25%" />
        <Column field="description" header="Description" style="width: 25%">
          <template #body="{ data }">
            {{ data.description || '-' }}
          </template>
        </Column>
        <Column field="sort_order" header="#" style="width: 10%" />
      </DataTable>
    </div>

    <!-- Step 3: Result -->
    <div v-if="step === 'result' && importResult" class="result-step">
      <div class="result-summary">
        <div class="summary-item">
          <span class="summary-value success">{{ importResult.succeeded }}</span>
          <span class="summary-label">Created</span>
        </div>
        <div class="summary-item">
          <span class="summary-value error">{{ importResult.failed }}</span>
          <span class="summary-label">Failed</span>
        </div>
      </div>

      <DataTable
        v-if="importResult.results.some(r => r.status !== 'created')"
        :value="importResult.results.filter(r => r.status !== 'created')"
        striped-rows
        size="small"
      >
        <Column field="code" header="Code" style="width: 30%" />
        <Column field="status" header="Status" style="width: 20%">
          <template #body="{ data }">
            <Tag :value="data.status" :severity="getResultSeverity(data.status)" />
          </template>
        </Column>
        <Column field="error" header="Error" style="width: 50%">
          <template #body="{ data }">
            {{ data.error || '-' }}
          </template>
        </Column>
      </DataTable>
    </div>

    <template #footer>
      <Button
        label="Cancel"
        severity="secondary"
        text
        @click="close"
        v-if="step !== 'result'"
      />
      <Button
        label="Import"
        icon="pi pi-upload"
        @click="doImport"
        :loading="importing"
        v-if="step === 'preview'"
      />
      <Button
        label="Done"
        @click="close"
        v-if="step === 'result'"
      />
    </template>
  </Dialog>
</template>

<style scoped>
.input-step {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.file-upload-section {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.or-divider {
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
}

.input-textarea {
  width: 100%;
  font-family: monospace;
  font-size: 0.875rem;
}

.parse-btn {
  margin-top: 0.5rem;
}

.preview-step {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.preview-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.result-step {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.result-summary {
  display: flex;
  gap: 2rem;
  justify-content: center;
}

.summary-item {
  display: flex;
  flex-direction: column;
  align-items: center;
}

.summary-value {
  font-size: 1.75rem;
  font-weight: 600;
}

.summary-value.success {
  color: var(--p-green-500);
}

.summary-value.error {
  color: var(--p-red-500);
}

.summary-label {
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
}
</style>
