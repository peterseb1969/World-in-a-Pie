<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import Card from 'primevue/card'
import FileUpload from 'primevue/fileupload'
import Button from 'primevue/button'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Tag from 'primevue/tag'
import Checkbox from 'primevue/checkbox'
import Message from 'primevue/message'
import { defStoreClient } from '@/api/client'
import { useUiStore } from '@/stores'
import type { ImportTerminologyRequest, CreateTerminologyRequest, CreateTermRequest, BulkResponse } from '@/types'

const router = useRouter()
const uiStore = useUiStore()

const step = ref<'upload' | 'preview' | 'importing' | 'result'>('upload')
const fileContent = ref<ImportTerminologyRequest | null>(null)
const fileName = ref('')
const importing = ref(false)
const importResult = ref<{
  terminology: { terminology_id: string; value: string; label: string }
  terms_result: BulkResponse
  relationships_result?: { total: number; created: number; skipped: number; errors: number }
} | null>(null)

// Progress tracking
const BATCH_SIZE = 1000
const progress = ref({
  current: 0,
  total: 0,
  succeeded: 0,
  failed: 0,
  skipped: 0
})

const options = ref({
  skip_duplicates: true,
  update_existing: false
})

const previewTerms = computed(() => {
  if (!fileContent.value) return []
  return fileContent.value.terms.slice(0, 20)
})

async function onFileSelect(event: { files: File[] }) {
  const file = event.files[0]
  if (!file) return

  fileName.value = file.name

  try {
    const text = await file.text()

    if (file.name.endsWith('.json')) {
      const data = JSON.parse(text)

      // Check if it's an export format (has 'terminology' and 'terms' at root)
      if (data.terminology && Array.isArray(data.terms)) {
        fileContent.value = {
          terminology: data.terminology,
          terms: data.terms
        }
      } else if (data.value && data.label) {
        // It's just a terminology object
        fileContent.value = {
          terminology: data as CreateTerminologyRequest,
          terms: []
        }
      } else {
        throw new Error('Invalid JSON format. Expected terminology data with value and label.')
      }
    } else if (file.name.endsWith('.csv')) {
      // Parse CSV - assume header row
      const lines = text.split('\n').filter(l => l.trim())
      if (lines.length < 2) {
        throw new Error('CSV must have a header row and at least one data row')
      }

      const headers = lines[0].split(',').map(h => h.trim().toLowerCase())
      const requiredHeaders = ['value', 'label']
      const missingHeaders = requiredHeaders.filter(h => !headers.includes(h))

      if (missingHeaders.length > 0) {
        throw new Error(`CSV missing required columns: ${missingHeaders.join(', ')}`)
      }

      // Extract terminology name from filename
      const termCode = file.name.replace('.csv', '').toUpperCase().replace(/[^A-Z0-9_]/g, '_')

      const terms: CreateTermRequest[] = []
      for (let i = 1; i < lines.length; i++) {
        const values = lines[i].split(',').map(v => v.trim())
        const term: CreateTermRequest = {
          value: values[headers.indexOf('value')] || '',
          label: values[headers.indexOf('label')] || '',
          description: headers.includes('description') ? values[headers.indexOf('description')] : undefined,
          sort_order: i - 1
        }
        if (term.value && term.label) {
          terms.push(term)
        }
      }

      fileContent.value = {
        terminology: {
          value: termCode,
          label: termCode.replace(/_/g, ' ')
        },
        terms
      }
    } else {
      throw new Error('Unsupported file format. Use .json or .csv')
    }

    step.value = 'preview'
    uiStore.showSuccess('File Loaded', `Found ${fileContent.value?.terms.length || 0} terms`)
  } catch (e) {
    uiStore.showError('Parse Error', (e as Error).message)
    fileContent.value = null
  }
}

async function doImport() {
  if (!fileContent.value) return

  importing.value = true
  const allTerms = fileContent.value.terms
  progress.value = {
    current: 0,
    total: allTerms.length,
    succeeded: 0,
    failed: 0,
    skipped: 0
  }
  step.value = 'importing'

  try {
    // Step 1: Create terminology with first batch of terms
    const firstBatch = allTerms.slice(0, BATCH_SIZE)
    const request: ImportTerminologyRequest = {
      terminology: fileContent.value.terminology,
      terms: firstBatch,
      options: options.value
    }

    const result = await defStoreClient.importTerminology(request)
    const terminologyId = result.terminology.terminology_id

    // Update progress from first batch
    progress.value.current = firstBatch.length
    progress.value.succeeded = result.terms_result.succeeded
    progress.value.failed = result.terms_result.failed
    progress.value.skipped = result.terms_result.skipped || 0

    // Collect all results
    const allResults = [...result.terms_result.results]

    // Step 2: Send remaining batches using bulk endpoint
    for (let i = BATCH_SIZE; i < allTerms.length; i += BATCH_SIZE) {
      const batch = allTerms.slice(i, i + BATCH_SIZE)

      const batchResult = await defStoreClient.bulkCreateTerms(terminologyId, batch)

      // Update progress
      progress.value.current = Math.min(i + batch.length, allTerms.length)
      progress.value.succeeded += batchResult.succeeded
      progress.value.failed += batchResult.failed
      progress.value.skipped += batchResult.skipped || 0

      // Collect results (limit to avoid memory issues)
      if (allResults.length < 1000) {
        allResults.push(...batchResult.results.slice(0, 1000 - allResults.length))
      }
    }

    // Build final result
    importResult.value = {
      terminology: result.terminology,
      terms_result: {
        results: allResults,
        total: allTerms.length,
        succeeded: progress.value.succeeded,
        failed: progress.value.failed,
        skipped: progress.value.skipped
      }
    }

    step.value = 'result'
    uiStore.showSuccess('Import Complete', `Created ${progress.value.succeeded} terms`)
  } catch (e) {
    uiStore.showError('Import Failed', (e as Error).message)
    step.value = 'preview' // Go back to preview on error
  } finally {
    importing.value = false
  }
}

function reset() {
  step.value = 'upload'
  fileContent.value = null
  fileName.value = ''
  importResult.value = null
}

function viewTerminology() {
  if (importResult.value) {
    router.push(`/terminologies/${importResult.value.terminology.terminology_id}`)
  }
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
  <div class="import-view">
    <h1>Import Terminology</h1>

    <!-- Step 1: Upload -->
    <Card v-if="step === 'upload'" class="upload-card">
      <template #title>Upload File</template>
      <template #subtitle>Import a terminology from JSON or CSV file</template>
      <template #content>
        <FileUpload
          mode="basic"
          accept=".json,.csv"
          :auto="true"
          choose-label="Select File"
          custom-upload
          @select="onFileSelect"
        />

        <div class="format-info">
          <h4>Supported Formats</h4>
          <div class="format-examples">
            <div class="format-example">
              <strong>JSON</strong>
              <pre>{
  "terminology": {
    "value": "STATUS",
    "label": "Status",
    "description": "Status codes"
  },
  "terms": [
    { "value": "active", "label": "Active" }
  ]
}</pre>
            </div>
            <div class="format-example">
              <strong>CSV</strong>
              <pre>value,label,description
active,Active,Currently active
inactive,Inactive,Not active</pre>
            </div>
          </div>
        </div>
      </template>
    </Card>

    <!-- Step 2: Preview -->
    <Card v-if="step === 'preview' && fileContent" class="preview-card">
      <template #title>Preview Import</template>
      <template #subtitle>{{ fileName }}</template>
      <template #content>
        <div class="preview-content">
          <div class="terminology-preview">
            <h4>Terminology</h4>
            <div class="preview-info">
              <span class="label">Value:</span>
              <span class="code-badge">{{ fileContent.terminology.value }}</span>
            </div>
            <div class="preview-info">
              <span class="label">Label:</span>
              <span>{{ fileContent.terminology.label }}</span>
            </div>
            <div v-if="fileContent.terminology.description" class="preview-info">
              <span class="label">Description:</span>
              <span>{{ fileContent.terminology.description }}</span>
            </div>
          </div>

          <div class="terms-preview">
            <h4>Terms ({{ fileContent.terms.length }} total)</h4>
            <DataTable :value="previewTerms" striped-rows size="small">
              <Column field="value" header="Value" style="width: 30%" />
              <Column field="label" header="Label" style="width: 40%" />
              <Column field="description" header="Description" style="width: 30%">
                <template #body="{ data }">
                  {{ data.description || '-' }}
                </template>
              </Column>
            </DataTable>
            <p v-if="fileContent.terms.length > 20" class="more-terms">
              ... and {{ fileContent.terms.length - 20 }} more terms
            </p>
          </div>

          <div class="import-options">
            <h4>Import Options</h4>
            <div class="option-item">
              <Checkbox v-model="options.skip_duplicates" binary input-id="skip-dup" />
              <label for="skip-dup">Skip duplicate terms (by value)</label>
            </div>
            <div class="option-item">
              <Checkbox v-model="options.update_existing" binary input-id="update-ex" />
              <label for="update-ex">Update existing terms if found</label>
            </div>
          </div>
        </div>
      </template>
      <template #footer>
        <div class="card-actions">
          <Button label="Back" severity="secondary" text @click="reset" />
          <Button label="Import" icon="pi pi-upload" :loading="importing" @click="doImport" />
        </div>
      </template>
    </Card>

    <!-- Step 3: Importing Progress -->
    <Card v-if="step === 'importing'" class="progress-card">
      <template #title>Importing...</template>
      <template #content>
        <div class="progress-content">
          <div class="progress-stats">
            <span class="progress-main">{{ progress.current.toLocaleString() }} of {{ progress.total.toLocaleString() }}</span>
            <span class="progress-percent">{{ Math.round((progress.current / progress.total) * 100) }}%</span>
          </div>
          <div class="progress-bar">
            <div
              class="progress-fill"
              :style="{ width: `${(progress.current / progress.total) * 100}%` }"
            ></div>
          </div>
          <div class="progress-details">
            <span class="detail success">{{ progress.succeeded.toLocaleString() }} created</span>
            <span class="detail" v-if="progress.skipped > 0">{{ progress.skipped.toLocaleString() }} skipped</span>
            <span class="detail error" v-if="progress.failed > 0">{{ progress.failed.toLocaleString() }} failed</span>
          </div>
        </div>
      </template>
    </Card>

    <!-- Step 4: Result -->
    <Card v-if="step === 'result' && importResult" class="result-card">
      <template #title>Import Complete</template>
      <template #content>
        <Message severity="success" :closable="false">
          Successfully imported terminology "{{ importResult.terminology.label }}"
        </Message>

        <div class="result-summary">
          <div class="summary-item">
            <span class="summary-value success">{{ importResult.terms_result.succeeded }}</span>
            <span class="summary-label">Created</span>
          </div>
          <div class="summary-item">
            <span class="summary-value error">{{ importResult.terms_result.failed }}</span>
            <span class="summary-label">Failed</span>
          </div>
          <div class="summary-item">
            <span class="summary-value">{{ importResult.terms_result.total }}</span>
            <span class="summary-label">Total</span>
          </div>
        </div>

        <div v-if="importResult.relationships_result" class="result-summary" style="margin-top: 1rem;">
          <h4 style="width: 100%; margin: 0 0 0.5rem 0; font-size: 0.875rem; text-transform: uppercase; color: var(--p-text-muted-color);">Relationships</h4>
          <div class="summary-item">
            <span class="summary-value success">{{ importResult.relationships_result.created }}</span>
            <span class="summary-label">Created</span>
          </div>
          <div class="summary-item" v-if="importResult.relationships_result.skipped > 0">
            <span class="summary-value">{{ importResult.relationships_result.skipped }}</span>
            <span class="summary-label">Skipped</span>
          </div>
          <div class="summary-item" v-if="importResult.relationships_result.errors > 0">
            <span class="summary-value error">{{ importResult.relationships_result.errors }}</span>
            <span class="summary-label">Errors</span>
          </div>
          <div class="summary-item">
            <span class="summary-value">{{ importResult.relationships_result.total }}</span>
            <span class="summary-label">Total</span>
          </div>
        </div>

        <div v-if="importResult.terms_result.results.length > 0" class="result-details">
          <h4>Details</h4>
          <DataTable :value="importResult.terms_result.results.slice(0, 50)" striped-rows size="small">
            <Column field="value" header="Value" style="width: 30%" />
            <Column field="status" header="Status" style="width: 20%">
              <template #body="{ data }">
                <Tag :value="data.status" :severity="getResultSeverity(data.status)" />
              </template>
            </Column>
            <Column field="error" header="Error" style="width: 50%">
              <template #body="{ data }">
                <span class="error-text">{{ data.error || '-' }}</span>
              </template>
            </Column>
          </DataTable>
        </div>
      </template>
      <template #footer>
        <div class="card-actions">
          <Button label="Import Another" severity="secondary" @click="reset" />
          <Button label="View Terminology" icon="pi pi-eye" @click="viewTerminology" />
        </div>
      </template>
    </Card>
  </div>
</template>

<style scoped>
.import-view {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
  max-width: 900px;
}

.import-view h1 {
  margin: 0;
}

.format-info {
  margin-top: 2rem;
  padding-top: 1.5rem;
  border-top: 1px solid var(--p-surface-border);
}

.format-info h4 {
  margin: 0 0 1rem 0;
  color: var(--p-text-muted-color);
}

.format-examples {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 1rem;
}

.format-example {
  background: var(--p-surface-50);
  padding: 1rem;
  border-radius: 6px;
}

.format-example strong {
  display: block;
  margin-bottom: 0.5rem;
}

.format-example pre {
  margin: 0;
  font-size: 0.75rem;
  overflow-x: auto;
  white-space: pre-wrap;
}

.preview-content {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.terminology-preview h4,
.terms-preview h4,
.import-options h4,
.result-details h4 {
  margin: 0 0 0.75rem 0;
  font-size: 0.875rem;
  text-transform: uppercase;
  color: var(--p-text-muted-color);
  letter-spacing: 0.05em;
}

.preview-info {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}

.preview-info .label {
  color: var(--p-text-muted-color);
  min-width: 100px;
}

.code-badge {
  font-family: monospace;
  background: var(--p-surface-100);
  padding: 0.125rem 0.375rem;
  border-radius: 3px;
  font-size: 0.875rem;
}

.more-terms {
  color: var(--p-text-muted-color);
  font-style: italic;
  margin: 0.5rem 0 0 0;
}

.import-options {
  background: var(--p-surface-50);
  padding: 1rem;
  border-radius: 6px;
}

.option-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}

.option-item:last-child {
  margin-bottom: 0;
}

.option-item label {
  cursor: pointer;
}

.card-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
}

.result-summary {
  display: flex;
  gap: 2rem;
  margin: 1.5rem 0;
}

.summary-item {
  display: flex;
  flex-direction: column;
  align-items: center;
}

.summary-value {
  font-size: 2rem;
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

.error-text {
  color: var(--p-red-500);
  font-size: 0.875rem;
}

.progress-content {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  padding: 1rem 0;
}

.progress-stats {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}

.progress-main {
  font-size: 1.5rem;
  font-weight: 600;
}

.progress-percent {
  font-size: 1.25rem;
  color: var(--p-text-muted-color);
}

.progress-bar {
  height: 8px;
  background: var(--p-surface-200);
  border-radius: 4px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: var(--p-primary-color);
  border-radius: 4px;
  transition: width 0.3s ease;
}

.progress-details {
  display: flex;
  gap: 1.5rem;
  font-size: 0.875rem;
}

.progress-details .detail {
  color: var(--p-text-muted-color);
}

.progress-details .detail.success {
  color: var(--p-green-500);
}

.progress-details .detail.error {
  color: var(--p-red-500);
}
</style>
