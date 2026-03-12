<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import Card from 'primevue/card'
import FileUpload from 'primevue/fileupload'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Checkbox from 'primevue/checkbox'
import Message from 'primevue/message'
import Tag from 'primevue/tag'
import { defStoreClient } from '@/api/client'
import { useUiStore, useNamespaceStore } from '@/stores'

const router = useRouter()
const uiStore = useUiStore()
const nsStore = useNamespaceStore()

const step = ref<'upload' | 'preview' | 'importing' | 'result'>('upload')
const fileName = ref('')
const importing = ref(false)

// Parsed OBO data
const oboData = ref<Record<string, unknown> | null>(null)
const preview = ref<{
  prefix: string | null
  title: string | null
  description: string | null
  version: string | null
  nodeCount: number
  edgeCount: number
  predicates: Record<string, number>
} | null>(null)

// Options
const options = ref({
  terminology_value: '',
  terminology_label: '',
  prefix_filter: '',
  include_deprecated: false,
  max_synonyms: 10,
  skip_duplicates: true,
  update_existing: false,
})

// Result
const importResult = ref<{
  terminology: { terminology_id: string; value: string; label: string; status: string }
  terms: { total: number; created: number; skipped: number; errors: number }
  relationships: { total: number; created: number; skipped: number; errors: number; predicate_distribution: Record<string, number>; error_samples?: string[] }
  elapsed_seconds: number
} | null>(null)

function detectPrefix(graph: Record<string, unknown>): string | null {
  const graphId = (graph.id as string) || ''
  const filename = graphId.split('/').pop() || ''
  const base = filename.split('.')[0].split('-')[0].toUpperCase()
  return base || null
}

function parsePreview(data: Record<string, unknown>) {
  const graphs = data.graphs as Array<Record<string, unknown>>
  if (!graphs || graphs.length === 0) return null

  const graph = graphs[0]
  const meta = (graph.meta as Record<string, unknown>) || {}
  const nodes = (graph.nodes as Array<Record<string, unknown>>) || []
  const edges = (graph.edges as Array<Record<string, unknown>>) || []

  const prefix = detectPrefix(graph)

  // Extract metadata
  let title: string | null = null
  let description: string | null = null
  let version: string | null = null
  const bpvs = (meta.basicPropertyValues as Array<Record<string, string>>) || []
  for (const bpv of bpvs) {
    if (bpv.pred?.includes('title')) title = bpv.val
    else if (bpv.pred?.includes('description')) description = bpv.val
    else if (bpv.pred?.includes('versionInfo')) version = bpv.val
  }

  // Count CLASS nodes matching prefix
  const uriPrefix = prefix ? `http://purl.obolibrary.org/obo/${prefix}_` : null
  const classNodes = nodes.filter(n => {
    if (n.type !== 'CLASS') return false
    if (uriPrefix && !(n.id as string).startsWith(uriPrefix)) return false
    return true
  })

  // Count predicates
  const predicates: Record<string, number> = {}
  for (const e of edges) {
    const pred = (e.pred as string) || 'unknown'
    const short = pred === 'is_a' ? 'is_a' : pred.split('/').pop() || pred
    predicates[short] = (predicates[short] || 0) + 1
  }

  return {
    prefix,
    title,
    description,
    version,
    nodeCount: classNodes.length,
    edgeCount: edges.length,
    predicates,
  }
}

async function onFileSelect(event: { files: File[] }) {
  const file = event.files[0]
  if (!file) return

  fileName.value = file.name

  try {
    const text = await file.text()
    const data = JSON.parse(text) as Record<string, unknown>

    if (!data.graphs || !Array.isArray(data.graphs) || data.graphs.length === 0) {
      throw new Error('Invalid OBO Graph JSON: missing "graphs" array')
    }

    oboData.value = data
    const parsed = parsePreview(data)
    if (!parsed) {
      throw new Error('Could not parse OBO Graph structure')
    }

    preview.value = parsed

    // Pre-fill options from detected values
    if (parsed.prefix) {
      options.value.terminology_value = parsed.prefix
      options.value.prefix_filter = parsed.prefix
    }
    if (parsed.title) {
      options.value.terminology_label = parsed.title
    }

    step.value = 'preview'
    uiStore.showSuccess('File Loaded', `${parsed.nodeCount.toLocaleString()} terms, ${parsed.edgeCount.toLocaleString()} relationships`)
  } catch (e) {
    uiStore.showError('Parse Error', (e as Error).message)
    oboData.value = null
    preview.value = null
  }
}

async function doImport() {
  if (!oboData.value) return

  importing.value = true
  step.value = 'importing'

  try {
    const opts: Record<string, unknown> = {
      skip_duplicates: options.value.skip_duplicates,
      update_existing: options.value.update_existing,
      include_deprecated: options.value.include_deprecated,
      max_synonyms: options.value.max_synonyms,
      namespace: nsStore.current,
    }
    if (options.value.terminology_value) opts.terminology_value = options.value.terminology_value
    if (options.value.terminology_label) opts.terminology_label = options.value.terminology_label
    if (options.value.prefix_filter) opts.prefix_filter = options.value.prefix_filter

    const result = await defStoreClient.importOntology(oboData.value, opts as any)
    importResult.value = result
    step.value = 'result'
    uiStore.showSuccess(
      'Import Complete',
      `${result.terms.created} terms, ${result.relationships.created} relationships in ${result.elapsed_seconds}s`
    )
  } catch (e) {
    uiStore.showError('Import Failed', (e as Error).message)
    step.value = 'preview'
  } finally {
    importing.value = false
  }
}

function reset() {
  step.value = 'upload'
  oboData.value = null
  preview.value = null
  fileName.value = ''
  importResult.value = null
}

function viewTerminology() {
  if (importResult.value) {
    router.push(`/terminologies/${importResult.value.terminology.terminology_id}`)
  }
}

const sortedPredicates = computed(() => {
  if (!preview.value) return []
  return Object.entries(preview.value.predicates)
    .sort((a, b) => b[1] - a[1])
})

const sortedResultPredicates = computed(() => {
  if (!importResult.value) return []
  return Object.entries(importResult.value.relationships.predicate_distribution)
    .sort((a, b) => b[1] - a[1])
})
</script>

<template>
  <div class="import-view">
    <h1>Import Ontology</h1>

    <!-- Step 1: Upload -->
    <Card v-if="step === 'upload'" class="upload-card">
      <template #title>Upload OBO Graph JSON</template>
      <template #subtitle>Import an ontology file (HP, GO, CHEBI, etc.) in OBO Graph JSON format</template>
      <template #content>
        <FileUpload
          mode="basic"
          accept=".json"
          :auto="true"
          choose-label="Select OBO Graph JSON"
          custom-upload
          @select="onFileSelect"
        />

        <div class="format-info">
          <h4>About OBO Graph JSON</h4>
          <p>
            Standard format for biomedical ontologies. Download from the
            OBO Foundry or convert from OWL using ROBOT.
          </p>
          <p class="hint">
            Structure: <code>graphs[0].nodes[]</code> (terms) and <code>graphs[0].edges[]</code> (relationships).
            Terms are imported with their labels, descriptions, synonyms, and cross-references.
            Relationships (is_a, part_of, etc.) are preserved.
          </p>
        </div>
      </template>
    </Card>

    <!-- Step 2: Preview -->
    <Card v-if="step === 'preview' && preview" class="preview-card">
      <template #title>Preview Ontology</template>
      <template #subtitle>{{ fileName }}</template>
      <template #content>
        <div class="preview-content">
          <div class="ontology-meta">
            <h4>Detected Ontology</h4>
            <div class="meta-grid">
              <div class="meta-item">
                <span class="label">Prefix:</span>
                <Tag :value="preview.prefix || 'Unknown'" />
              </div>
              <div v-if="preview.title" class="meta-item">
                <span class="label">Title:</span>
                <span>{{ preview.title }}</span>
              </div>
              <div v-if="preview.version" class="meta-item">
                <span class="label">Version:</span>
                <span>{{ preview.version }}</span>
              </div>
              <div class="meta-item">
                <span class="label">Terms:</span>
                <span class="count">{{ preview.nodeCount.toLocaleString() }}</span>
              </div>
              <div class="meta-item">
                <span class="label">Relationships:</span>
                <span class="count">{{ preview.edgeCount.toLocaleString() }}</span>
              </div>
            </div>

            <div v-if="sortedPredicates.length > 0" class="predicate-summary">
              <h4>Relationship Types</h4>
              <div class="predicate-list">
                <Tag
                  v-for="[pred, count] in sortedPredicates"
                  :key="pred"
                  :value="`${pred}: ${count.toLocaleString()}`"
                  severity="secondary"
                />
              </div>
            </div>
          </div>

          <div class="import-options">
            <h4>Import Options</h4>
            <div class="option-row">
              <label for="term-value">Terminology Value</label>
              <InputText id="term-value" v-model="options.terminology_value" placeholder="e.g., HPO" />
            </div>
            <div class="option-row">
              <label for="term-label">Terminology Label</label>
              <InputText id="term-label" v-model="options.terminology_label" placeholder="e.g., Human Phenotype Ontology" />
            </div>
            <div class="option-row">
              <label for="prefix-filter">Prefix Filter</label>
              <InputText id="prefix-filter" v-model="options.prefix_filter" placeholder="Only import nodes with this prefix" />
            </div>
            <div class="option-row">
              <label for="max-syn">Max Synonyms per Term</label>
              <InputText id="max-syn" :model-value="String(options.max_synonyms)" @update:model-value="v => options.max_synonyms = Number(v) || 10" type="number" />
            </div>
            <div class="option-checks">
              <div class="option-item">
                <Checkbox v-model="options.skip_duplicates" binary input-id="skip-dup" />
                <label for="skip-dup">Skip duplicate terms</label>
              </div>
              <div class="option-item">
                <Checkbox v-model="options.update_existing" binary input-id="update-ex" />
                <label for="update-ex">Update existing terms</label>
              </div>
              <div class="option-item">
                <Checkbox v-model="options.include_deprecated" binary input-id="inc-dep" />
                <label for="inc-dep">Include deprecated/obsolete terms</label>
              </div>
            </div>
          </div>

          <Message v-if="preview.nodeCount > 50000" severity="warn" :closable="false">
            Large ontology detected ({{ preview.nodeCount.toLocaleString() }} terms).
            Import may take several minutes.
          </Message>
        </div>
      </template>
      <template #footer>
        <div class="card-actions">
          <Button label="Back" severity="secondary" text @click="reset" />
          <Button label="Import Ontology" icon="pi pi-upload" :loading="importing" @click="doImport" />
        </div>
      </template>
    </Card>

    <!-- Step 3: Importing -->
    <Card v-if="step === 'importing'" class="progress-card">
      <template #title>Importing Ontology...</template>
      <template #content>
        <div class="progress-content">
          <div class="importing-spinner">
            <i class="pi pi-spin pi-spinner" style="font-size: 2rem"></i>
            <p>Importing {{ preview?.nodeCount.toLocaleString() }} terms and {{ preview?.edgeCount.toLocaleString() }} relationships...</p>
            <p class="hint">This may take a while for large ontologies.</p>
          </div>
        </div>
      </template>
    </Card>

    <!-- Step 4: Result -->
    <Card v-if="step === 'result' && importResult" class="result-card">
      <template #title>Import Complete</template>
      <template #content>
        <Message severity="success" :closable="false">
          Ontology "{{ importResult.terminology.label }}" imported in {{ importResult.elapsed_seconds }}s
        </Message>

        <div class="result-sections">
          <div class="result-section">
            <h4>Terminology</h4>
            <div class="meta-grid">
              <div class="meta-item">
                <span class="label">Value:</span>
                <span class="code-badge">{{ importResult.terminology.value }}</span>
              </div>
              <div class="meta-item">
                <span class="label">Status:</span>
                <Tag :value="importResult.terminology.status" :severity="importResult.terminology.status === 'created' ? 'success' : 'info'" />
              </div>
            </div>
          </div>

          <div class="result-section">
            <h4>Terms</h4>
            <div class="result-summary">
              <div class="summary-item">
                <span class="summary-value success">{{ importResult.terms.created.toLocaleString() }}</span>
                <span class="summary-label">Created</span>
              </div>
              <div class="summary-item" v-if="importResult.terms.skipped > 0">
                <span class="summary-value">{{ importResult.terms.skipped.toLocaleString() }}</span>
                <span class="summary-label">Skipped</span>
              </div>
              <div class="summary-item" v-if="importResult.terms.errors > 0">
                <span class="summary-value error">{{ importResult.terms.errors.toLocaleString() }}</span>
                <span class="summary-label">Errors</span>
              </div>
              <div class="summary-item">
                <span class="summary-value">{{ importResult.terms.total.toLocaleString() }}</span>
                <span class="summary-label">Total</span>
              </div>
            </div>
          </div>

          <div class="result-section">
            <h4>Relationships</h4>
            <div class="result-summary">
              <div class="summary-item">
                <span class="summary-value success">{{ importResult.relationships.created.toLocaleString() }}</span>
                <span class="summary-label">Created</span>
              </div>
              <div class="summary-item" v-if="importResult.relationships.skipped > 0">
                <span class="summary-value">{{ importResult.relationships.skipped.toLocaleString() }}</span>
                <span class="summary-label">Skipped</span>
              </div>
              <div class="summary-item" v-if="importResult.relationships.errors > 0">
                <span class="summary-value error">{{ importResult.relationships.errors.toLocaleString() }}</span>
                <span class="summary-label">Errors</span>
              </div>
              <div class="summary-item">
                <span class="summary-value">{{ importResult.relationships.total.toLocaleString() }}</span>
                <span class="summary-label">Total</span>
              </div>
            </div>

            <div v-if="sortedResultPredicates.length > 0" class="predicate-summary">
              <div class="predicate-list">
                <Tag
                  v-for="[pred, count] in sortedResultPredicates"
                  :key="pred"
                  :value="`${pred}: ${count.toLocaleString()}`"
                  severity="secondary"
                />
              </div>
            </div>

            <div v-if="importResult.relationships.error_samples && importResult.relationships.error_samples.length > 0" class="error-samples">
              <h4>Error Samples</h4>
              <ul>
                <li v-for="(err, idx) in importResult.relationships.error_samples" :key="idx" class="error-text">
                  {{ err }}
                </li>
              </ul>
            </div>
          </div>
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
  margin: 0 0 0.5rem 0;
  color: var(--p-text-muted-color);
}

.format-info p {
  margin: 0 0 0.5rem 0;
  color: var(--p-text-secondary-color);
}

.format-info .hint {
  font-size: 0.875rem;
  color: var(--p-text-muted-color);
}

.format-info code {
  background: var(--p-surface-100);
  padding: 0.125rem 0.375rem;
  border-radius: 3px;
  font-size: 0.8125rem;
}

.preview-content {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.ontology-meta h4,
.import-options h4,
.result-section h4 {
  margin: 0 0 0.75rem 0;
  font-size: 0.875rem;
  text-transform: uppercase;
  color: var(--p-text-muted-color);
  letter-spacing: 0.05em;
}

.meta-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 0.75rem;
}

.meta-item {
  display: flex;
  gap: 0.5rem;
  align-items: center;
}

.meta-item .label {
  color: var(--p-text-muted-color);
  min-width: 80px;
}

.meta-item .count {
  font-weight: 600;
  font-size: 1.125rem;
}

.predicate-summary {
  margin-top: 1rem;
}

.predicate-list {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.import-options {
  background: var(--p-surface-50);
  padding: 1rem;
  border-radius: 6px;
}

.option-row {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  margin-bottom: 0.75rem;
}

.option-row label {
  font-size: 0.875rem;
  color: var(--p-text-muted-color);
}

.option-checks {
  margin-top: 0.5rem;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.option-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.option-item label {
  cursor: pointer;
}

.card-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
}

.progress-content {
  padding: 2rem 0;
}

.importing-spinner {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1rem;
}

.importing-spinner p {
  margin: 0;
  color: var(--p-text-secondary-color);
}

.importing-spinner .hint {
  font-size: 0.875rem;
  color: var(--p-text-muted-color);
}

.result-sections {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
  margin-top: 1rem;
}

.result-summary {
  display: flex;
  gap: 2rem;
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

.code-badge {
  font-family: monospace;
  background: var(--p-surface-100);
  padding: 0.125rem 0.375rem;
  border-radius: 3px;
  font-size: 0.875rem;
}

.error-samples {
  margin-top: 1rem;
  background: var(--p-red-50);
  padding: 1rem;
  border-radius: 6px;
}

.error-samples ul {
  margin: 0;
  padding-left: 1.5rem;
}

.error-samples li {
  font-size: 0.875rem;
  color: var(--p-red-700);
  margin-bottom: 0.25rem;
}
</style>
