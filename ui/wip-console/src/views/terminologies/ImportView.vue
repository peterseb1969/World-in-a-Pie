<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import Card from 'primevue/card'
import FileUpload from 'primevue/fileupload'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Tag from 'primevue/tag'
import Checkbox from 'primevue/checkbox'
import Message from 'primevue/message'
import { defStoreClient } from '@/api/client'
import { useUiStore, useNamespaceStore } from '@/stores'
import type { ImportTerminologyRequest, CreateTermRequest, BulkResponse } from '@/types'

const router = useRouter()
const uiStore = useUiStore()
const nsStore = useNamespaceStore()

type DetectedFormat = 'wip_json' | 'obo_json' | 'csv'

const step = ref<'upload' | 'preview' | 'importing' | 'result'>('upload')
const detectedFormat = ref<DetectedFormat | null>(null)
const fileName = ref('')
const parsing = ref(false)
const importing = ref(false)

// WIP JSON / CSV parsed data
const wipData = ref<ImportTerminologyRequest | null>(null)

// OBO parsed data
const oboData = ref<Record<string, unknown> | null>(null)
const oboPreview = ref<{
  prefix: string | null
  title: string | null
  description: string | null
  version: string | null
  nodeCount: number
  edgeCount: number
  predicates: Record<string, number>
} | null>(null)

// Common options (all formats)
const commonOptions = ref({
  terminology_value: '',
  terminology_label: '',
  skip_duplicates: true,
  update_existing: false,
})

// OBO-specific options
const oboOptions = ref({
  prefix_filter: '',
  max_synonyms: 10,
  include_deprecated: false,
})

// Unified result
const importResult = ref<{
  terminology: { terminology_id: string; value: string; label: string; status?: string }
  terms: { total: number; created: number; skipped: number; failed: number }
  relationships?: {
    total: number; created: number; skipped: number; errors: number
    predicate_distribution?: Record<string, number>
    error_samples?: string[]
  }
  elapsed_seconds?: number
  term_details?: BulkResponse['results']
} | null>(null)

const formatLabel = computed(() => {
  switch (detectedFormat.value) {
    case 'obo_json': return 'OBO Graph JSON'
    case 'wip_json': return 'WIP JSON'
    case 'csv': return 'CSV'
    default: return ''
  }
})

const isOntology = computed(() => {
  if (detectedFormat.value === 'obo_json') return true
  if (detectedFormat.value === 'wip_json' && wipData.value?.relationships?.length) return true
  return false
})

const previewTerms = computed(() => {
  if (!wipData.value) return []
  return wipData.value.terms.slice(0, 20)
})

const sortedPredicates = computed(() => {
  if (!oboPreview.value) return []
  return Object.entries(oboPreview.value.predicates).sort((a, b) => b[1] - a[1])
})

const sortedResultPredicates = computed(() => {
  if (!importResult.value?.relationships?.predicate_distribution) return []
  return Object.entries(importResult.value.relationships.predicate_distribution).sort((a, b) => b[1] - a[1])
})

// --- OBO parsing (from OntologyImportView) ---

function detectPrefix(graph: Record<string, unknown>): string | null {
  const graphId = (graph.id as string) || ''
  const filename = graphId.split('/').pop() || ''
  const base = filename.split('.')[0].split('-')[0].toUpperCase()
  return base || null
}

function parseOboPreview(data: Record<string, unknown>) {
  const graphs = data.graphs as Array<Record<string, unknown>>
  if (!graphs || graphs.length === 0) return null

  const graph = graphs[0]
  const meta = (graph.meta as Record<string, unknown>) || {}
  const nodes = (graph.nodes as Array<Record<string, unknown>>) || []
  const edges = (graph.edges as Array<Record<string, unknown>>) || []

  const prefix = detectPrefix(graph)

  let title: string | null = null
  let description: string | null = null
  let version: string | null = null
  const bpvs = (meta.basicPropertyValues as Array<Record<string, string>>) || []
  for (const bpv of bpvs) {
    if (bpv.pred?.includes('title')) title = bpv.val
    else if (bpv.pred?.includes('description')) description = bpv.val
    else if (bpv.pred?.includes('versionInfo')) version = bpv.val
  }

  const uriPrefix = prefix ? `http://purl.obolibrary.org/obo/${prefix}_` : null
  const classNodes = nodes.filter(n => {
    if (n.type !== 'CLASS') return false
    if (uriPrefix && !(n.id as string).startsWith(uriPrefix)) return false
    return true
  })

  const predicates: Record<string, number> = {}
  for (const e of edges) {
    const pred = (e.pred as string) || 'unknown'
    const short = pred === 'is_a' ? 'is_a' : pred.split('/').pop() || pred
    predicates[short] = (predicates[short] || 0) + 1
  }

  return { prefix, title, description, version, nodeCount: classNodes.length, edgeCount: edges.length, predicates }
}

// --- File selection & auto-detection ---

async function onFileSelect(event: { files: File[] }) {
  const file = event.files[0]
  if (!file) return

  fileName.value = file.name
  parsing.value = true

  try {
    const text = await file.text()

    if (file.name.endsWith('.json')) {
      const data = JSON.parse(text)

      if (data.graphs && Array.isArray(data.graphs) && data.graphs.length > 0) {
        // OBO Graph JSON
        detectedFormat.value = 'obo_json'
        oboData.value = data
        const parsed = parseOboPreview(data)
        if (!parsed) throw new Error('Could not parse OBO Graph structure')
        oboPreview.value = parsed

        commonOptions.value.terminology_value = parsed.prefix || ''
        commonOptions.value.terminology_label = parsed.title || ''
        oboOptions.value.prefix_filter = parsed.prefix || ''

        uiStore.showSuccess('File Loaded', `Ontology: ${parsed.nodeCount.toLocaleString()} terms, ${parsed.edgeCount.toLocaleString()} relationships`)

      } else if (data.terminology && Array.isArray(data.terms)) {
        // WIP export JSON
        detectedFormat.value = 'wip_json'
        wipData.value = {
          terminology: data.terminology,
          terms: data.terms,
          relationships: data.relationships || undefined,
        }
        commonOptions.value.terminology_value = data.terminology.value
        commonOptions.value.terminology_label = data.terminology.label

        const relCount = data.relationships?.length || 0
        const msg = relCount > 0
          ? `${data.terms.length.toLocaleString()} terms, ${relCount.toLocaleString()} relationships`
          : `${data.terms.length.toLocaleString()} terms`
        uiStore.showSuccess('File Loaded', msg)

      } else {
        throw new Error('Unrecognized JSON format. Expected WIP export (terminology + terms) or OBO Graph JSON (graphs array).')
      }
    } else if (file.name.endsWith('.csv')) {
      // CSV
      detectedFormat.value = 'csv'
      const lines = text.split('\n').filter(l => l.trim())
      if (lines.length < 2) throw new Error('CSV must have a header row and at least one data row')

      const headers = lines[0].split(',').map(h => h.trim().toLowerCase())
      const missingHeaders = ['value', 'label'].filter(h => !headers.includes(h))
      if (missingHeaders.length > 0) throw new Error(`CSV missing required columns: ${missingHeaders.join(', ')}`)

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
        if (term.value && term.label) terms.push(term)
      }

      wipData.value = {
        terminology: { value: termCode, label: termCode.replace(/_/g, ' ') },
        terms,
      }
      commonOptions.value.terminology_value = termCode
      commonOptions.value.terminology_label = termCode.replace(/_/g, ' ')

      uiStore.showSuccess('File Loaded', `${terms.length.toLocaleString()} terms`)
    } else {
      throw new Error('Unsupported file format. Use .json or .csv')
    }

    step.value = 'preview'
  } catch (e) {
    uiStore.showError('Parse Error', (e as Error).message)
    resetData()
  } finally {
    parsing.value = false
  }
}

// --- Import ---

async function doImport() {
  importing.value = true
  step.value = 'importing'

  try {
    if (detectedFormat.value === 'obo_json') {
      await doOboImport()
    } else {
      await doWipImport()
    }
    step.value = 'result'
  } catch (e) {
    uiStore.showError('Import Failed', (e as Error).message)
    step.value = 'preview'
  } finally {
    importing.value = false
  }
}

async function doWipImport() {
  if (!wipData.value) return

  const ns = nsStore.currentNamespaceParam
  if (!ns) {
    throw new Error('Namespace is required for import. Please select a namespace (not "all").')
  }

  // Apply overrides from common options
  const terminology = { ...wipData.value.terminology }
  if (commonOptions.value.terminology_value) terminology.value = commonOptions.value.terminology_value
  if (commonOptions.value.terminology_label) terminology.label = commonOptions.value.terminology_label

  const request: ImportTerminologyRequest = {
    terminology: { ...terminology, namespace: ns },
    terms: wipData.value.terms,
    relationships: wipData.value.relationships,
    options: {
      skip_duplicates: commonOptions.value.skip_duplicates,
      update_existing: commonOptions.value.update_existing,
    },
  }

  const result = await defStoreClient.importTerminology(request)

  importResult.value = {
    terminology: result.terminology,
    terms: {
      total: result.terms_result.total,
      created: result.terms_result.succeeded,
      skipped: result.terms_result.skipped || 0,
      failed: result.terms_result.failed,
    },
    term_details: result.terms_result.results,
  }
  if (result.relationships_result) {
    importResult.value.relationships = result.relationships_result
  }

  uiStore.showSuccess('Import Complete', `Created ${result.terms_result.succeeded} terms`)
}

async function doOboImport() {
  if (!oboData.value) return

  const namespace = nsStore.currentNamespaceParam
  if (!namespace) {
    throw new Error('Namespace is required for import. Please select a namespace (not "all").')
  }
  const opts: Record<string, unknown> = {
    skip_duplicates: commonOptions.value.skip_duplicates,
    update_existing: commonOptions.value.update_existing,
    include_deprecated: oboOptions.value.include_deprecated,
    max_synonyms: oboOptions.value.max_synonyms,
    namespace,
  }
  if (commonOptions.value.terminology_value) opts.terminology_value = commonOptions.value.terminology_value
  if (commonOptions.value.terminology_label) opts.terminology_label = commonOptions.value.terminology_label
  if (oboOptions.value.prefix_filter) opts.prefix_filter = oboOptions.value.prefix_filter

  const result = await defStoreClient.importOntology(oboData.value, opts as any)

  importResult.value = {
    terminology: result.terminology,
    terms: {
      total: result.terms.total,
      created: result.terms.created,
      skipped: result.terms.skipped,
      failed: result.terms.errors,
    },
    relationships: {
      total: result.relationships.total,
      created: result.relationships.created,
      skipped: result.relationships.skipped,
      errors: result.relationships.errors,
      predicate_distribution: result.relationships.predicate_distribution,
      error_samples: result.relationships.error_samples,
    },
    elapsed_seconds: result.elapsed_seconds,
  }

  uiStore.showSuccess(
    'Import Complete',
    `${result.terms.created} terms, ${result.relationships.created} relationships in ${result.elapsed_seconds}s`
  )
}

// --- Helpers ---

function reset() {
  step.value = 'upload'
  resetData()
}

function resetData() {
  detectedFormat.value = null
  fileName.value = ''
  wipData.value = null
  oboData.value = null
  oboPreview.value = null
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
    <h1>Import</h1>

    <!-- Step 1: Upload -->
    <Card v-if="step === 'upload'" class="upload-card">
      <template #title>Upload File</template>
      <template #subtitle>Import a terminology or ontology from JSON or CSV</template>
      <template #content>
        <div v-if="parsing" class="parsing-spinner">
          <i class="pi pi-spin pi-spinner" style="font-size: 2rem"></i>
          <p>Parsing file...</p>
        </div>
        <template v-else>
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
                <strong>WIP JSON</strong>
                <pre>{ "terminology": {...}, "terms": [...] }</pre>
                <p class="hint">Exported from WIP, with optional relationships</p>
              </div>
              <div class="format-example">
                <strong>OBO Graph JSON</strong>
                <pre>{ "graphs": [{ "nodes": [...], "edges": [...] }] }</pre>
                <p class="hint">Standard ontology format (HP, GO, CHEBI, etc.)</p>
              </div>
              <div class="format-example">
                <strong>CSV</strong>
                <pre>value,label,description
active,Active,Currently active</pre>
                <p class="hint">Simple terminology with value, label columns</p>
              </div>
            </div>
          </div>
        </template>
      </template>
    </Card>

    <!-- Step 2: Preview -->
    <Card v-if="step === 'preview'" class="preview-card">
      <template #title>
        Preview
        <Tag :value="formatLabel" severity="info" class="format-tag" />
      </template>
      <template #subtitle>{{ fileName }}</template>
      <template #content>
        <div class="preview-content">

          <Message severity="warn" :closable="false">
            <template v-if="detectedFormat === 'obo_json'">
              OBO Graph JSON import is not lossless. Logical definitions, property values, and some
              cross-references may not be preserved. WIP stores a simplified representation of the ontology.
            </template>
            <template v-else-if="isOntology">
              This is a WIP export with relationships. Re-importing is lossless within WIP.
            </template>
            <template v-else>
              Terminology import preserves all term data (values, labels, aliases, descriptions).
            </template>
          </Message>

          <!-- OBO preview -->
          <div v-if="detectedFormat === 'obo_json' && oboPreview" class="ontology-meta">
            <h4>Detected Ontology</h4>
            <div class="meta-grid">
              <div class="meta-item">
                <span class="label">Prefix:</span>
                <Tag :value="oboPreview.prefix || 'Unknown'" />
              </div>
              <div v-if="oboPreview.title" class="meta-item">
                <span class="label">Title:</span>
                <span>{{ oboPreview.title }}</span>
              </div>
              <div v-if="oboPreview.version" class="meta-item">
                <span class="label">Version:</span>
                <span>{{ oboPreview.version }}</span>
              </div>
              <div class="meta-item">
                <span class="label">Terms:</span>
                <span class="count">{{ oboPreview.nodeCount.toLocaleString() }}</span>
              </div>
              <div class="meta-item">
                <span class="label">Relationships:</span>
                <span class="count">{{ oboPreview.edgeCount.toLocaleString() }}</span>
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

          <!-- WIP JSON / CSV preview -->
          <div v-if="(detectedFormat === 'wip_json' || detectedFormat === 'csv') && wipData" class="terminology-preview">
            <h4>{{ isOntology ? 'Detected Ontology' : 'Detected Terminology' }}</h4>
            <div class="meta-grid">
              <div class="meta-item">
                <span class="label">Value:</span>
                <span class="code-badge">{{ wipData.terminology.value }}</span>
              </div>
              <div class="meta-item">
                <span class="label">Label:</span>
                <span>{{ wipData.terminology.label }}</span>
              </div>
              <div class="meta-item">
                <span class="label">Terms:</span>
                <span class="count">{{ wipData.terms.length.toLocaleString() }}</span>
              </div>
              <div v-if="wipData.relationships?.length" class="meta-item">
                <span class="label">Relationships:</span>
                <span class="count">{{ wipData.relationships.length.toLocaleString() }}</span>
              </div>
            </div>

            <div v-if="wipData.terms.length > 0" class="terms-table">
              <h4>Terms Preview</h4>
              <DataTable :value="previewTerms" striped-rows size="small">
                <Column field="value" header="Value" style="width: 30%" />
                <Column field="label" header="Label" style="width: 40%" />
                <Column field="description" header="Description" style="width: 30%">
                  <template #body="{ data }">{{ data.description || '-' }}</template>
                </Column>
              </DataTable>
              <p v-if="wipData.terms.length > 20" class="more-terms">
                ... and {{ (wipData.terms.length - 20).toLocaleString() }} more terms
              </p>
            </div>
          </div>

          <!-- Options -->
          <div class="import-options">
            <h4>Import Options</h4>

            <div class="option-row">
              <label for="term-value">Terminology Value</label>
              <InputText id="term-value" v-model="commonOptions.terminology_value" placeholder="e.g., HP" />
            </div>
            <div class="option-row">
              <label for="term-label">Terminology Label</label>
              <InputText id="term-label" v-model="commonOptions.terminology_label" placeholder="e.g., Human Phenotype Ontology" />
            </div>

            <!-- OBO-specific options -->
            <template v-if="detectedFormat === 'obo_json'">
              <div class="option-row">
                <label for="prefix-filter">Prefix Filter</label>
                <InputText id="prefix-filter" v-model="oboOptions.prefix_filter" placeholder="Only import nodes with this prefix" />
              </div>
              <div class="option-row">
                <label for="max-syn">Max Synonyms per Term</label>
                <InputText id="max-syn" :model-value="String(oboOptions.max_synonyms)" @update:model-value="v => oboOptions.max_synonyms = Number(v) || 10" type="number" />
              </div>
            </template>

            <div class="option-checks">
              <div class="option-item">
                <Checkbox v-model="commonOptions.skip_duplicates" binary input-id="skip-dup" />
                <label for="skip-dup">Skip duplicate terms</label>
              </div>
              <div class="option-item">
                <Checkbox v-model="commonOptions.update_existing" binary input-id="update-ex" />
                <label for="update-ex">Update existing terms</label>
              </div>
              <div v-if="detectedFormat === 'obo_json'" class="option-item">
                <Checkbox v-model="oboOptions.include_deprecated" binary input-id="inc-dep" />
                <label for="inc-dep">Include deprecated/obsolete terms</label>
              </div>
            </div>
          </div>

          <Message v-if="oboPreview && oboPreview.nodeCount > 50000" severity="warn" :closable="false">
            Large ontology detected ({{ oboPreview.nodeCount.toLocaleString() }} terms).
            Import may take several minutes.
          </Message>
        </div>
      </template>
      <template #footer>
        <div class="card-actions">
          <Button label="Back" severity="secondary" text @click="reset" />
          <Button
            :label="isOntology ? 'Import Ontology' : 'Import Terminology'"
            icon="pi pi-upload"
            :loading="importing"
            @click="doImport"
          />
        </div>
      </template>
    </Card>

    <!-- Step 3: Importing -->
    <Card v-if="step === 'importing'" class="progress-card">
      <template #title>Importing...</template>
      <template #content>
        <div class="progress-content">
          <div class="importing-spinner">
            <i class="pi pi-spin pi-spinner" style="font-size: 2rem"></i>
            <template v-if="detectedFormat === 'obo_json' && oboPreview">
              <p>Importing {{ oboPreview.nodeCount.toLocaleString() }} terms and {{ oboPreview.edgeCount.toLocaleString() }} relationships...</p>
            </template>
            <template v-else-if="wipData">
              <p>Importing {{ wipData.terms.length.toLocaleString() }} terms<span v-if="wipData.relationships?.length"> and {{ wipData.relationships.length.toLocaleString() }} relationships</span>...</p>
            </template>
            <p class="hint">This may take a while for large imports.</p>
          </div>
        </div>
      </template>
    </Card>

    <!-- Step 4: Result -->
    <Card v-if="step === 'result' && importResult" class="result-card">
      <template #title>Import Complete</template>
      <template #content>
        <Message severity="success" :closable="false">
          <template v-if="importResult.elapsed_seconds">
            {{ isOntology ? 'Ontology' : 'Terminology' }} "{{ importResult.terminology.label }}" imported in {{ importResult.elapsed_seconds }}s
          </template>
          <template v-else>
            Successfully imported {{ isOntology ? 'ontology' : 'terminology' }} "{{ importResult.terminology.label }}"
          </template>
        </Message>

        <div class="result-sections">
          <div class="result-section">
            <h4>Terminology</h4>
            <div class="meta-grid">
              <div class="meta-item">
                <span class="label">Value:</span>
                <span class="code-badge">{{ importResult.terminology.value }}</span>
              </div>
              <div v-if="importResult.terminology.status" class="meta-item">
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
              <div class="summary-item" v-if="importResult.terms.failed > 0">
                <span class="summary-value error">{{ importResult.terms.failed.toLocaleString() }}</span>
                <span class="summary-label">Failed</span>
              </div>
              <div class="summary-item">
                <span class="summary-value">{{ importResult.terms.total.toLocaleString() }}</span>
                <span class="summary-label">Total</span>
              </div>
            </div>
          </div>

          <div v-if="importResult.relationships" class="result-section">
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

            <div v-if="importResult.relationships.error_samples?.length" class="error-samples">
              <h4>Error Samples</h4>
              <ul>
                <li v-for="(err, idx) in importResult.relationships.error_samples" :key="idx">{{ err }}</li>
              </ul>
            </div>
          </div>

          <!-- Per-item detail table (WIP/CSV only) -->
          <div v-if="importResult.term_details && importResult.term_details.length > 0" class="result-section">
            <h4>Details</h4>
            <DataTable :value="importResult.term_details.slice(0, 50)" striped-rows size="small">
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

.format-tag {
  margin-left: 0.5rem;
  vertical-align: middle;
}

/* Upload */
.parsing-spinner {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1rem;
  padding: 2rem 0;
}

.parsing-spinner p {
  margin: 0;
  color: var(--p-text-secondary-color);
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
  grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
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

.format-example .hint {
  margin: 0.5rem 0 0 0;
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
}

/* Preview */
.preview-content {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.ontology-meta h4,
.terminology-preview h4,
.terms-table h4,
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

.code-badge {
  font-family: monospace;
  background: var(--p-surface-100);
  padding: 0.125rem 0.375rem;
  border-radius: 3px;
  font-size: 0.875rem;
}

.predicate-summary {
  margin-top: 1rem;
}

.predicate-list {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.more-terms {
  color: var(--p-text-muted-color);
  font-style: italic;
  margin: 0.5rem 0 0 0;
}

/* Options */
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

/* Importing */
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

/* Results */
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

.error-text {
  color: var(--p-red-500);
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
