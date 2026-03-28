<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import Breadcrumb from 'primevue/breadcrumb'
import Card from 'primevue/card'
import Select from 'primevue/select'
import AutoComplete from 'primevue/autocomplete'
import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import Tag from 'primevue/tag'
import SelectButton from 'primevue/selectbutton'
import ProgressSpinner from 'primevue/progressspinner'
import { useUiStore, useNamespaceStore } from '@/stores'
import { defStoreClient, documentStoreClient } from '@/api/client'
import type { Terminology, Term } from '@/types'
import TruncatedId from '@/components/common/TruncatedId.vue'
import EgoGraph from '@/components/terminologies/EgoGraph.vue'

const router = useRouter()
const route = useRoute()
const uiStore = useUiStore()
const namespaceStore = useNamespaceStore()

// -------------------------------------------------------------------------
// State
// -------------------------------------------------------------------------

const terminologies = ref<Terminology[]>([])
const selectedTerminology = ref<Terminology | null>(null)
const loadingTerminologies = ref(false)

const termSearch = ref<Term | string | null>(null)
const termSuggestions = ref<Term[]>([])
const loadingSearch = ref(false)
const initialTerms = ref<Term[]>([])

const focusTermId = ref<string | null>(null)
const focusTerm = ref<Term | null>(null)
const depth = ref(2)
const depthOptions = [
  { label: '1', value: 1 },
  { label: '2', value: 2 },
  { label: '3', value: 3 },
]

const discoveredTypes = ref<string[]>([])
const visibleTypes = ref<string[]>([])
const allTypesVisible = computed(() => visibleTypes.value.length === discoveredTypes.value.length)

// Selected node detail (from hover)
const selectedNodeId = ref<string | null>(null)
const selectedNodeValue = ref('')

// Documents referencing focus term
interface TermDocument {
  document_id: string
  template_value: string
  data: Record<string, unknown>
  version: number
}
const termDocuments = ref<TermDocument[]>([])
const termDocumentsTotal = ref(0)
const loadingDocuments = ref(false)

// Edge colour map (matches EgoGraph)
const TYPE_COLOURS: Record<string, string> = {
  is_a: '#4a90d9',
  has_subtype: '#4a90d9',
  part_of: '#27ae60',
  has_part: '#27ae60',
  maps_to: '#e67e22',
  mapped_from: '#e67e22',
  related_to: '#95a5a6',
  finding_site: '#8e44ad',
  causative_agent: '#c0392b',
}

// Breadcrumbs
const breadcrumbHome = { icon: 'pi pi-home', command: () => { router.push('/') } }
const breadcrumbItems = computed(() => [
  { label: 'Terminologies', command: () => { router.push('/terminologies') } },
  { label: 'Ontology Browser' },
])

// -------------------------------------------------------------------------
// Load terminologies
// -------------------------------------------------------------------------

async function loadTerminologies() {
  loadingTerminologies.value = true
  try {
    let page = 1
    const all: Terminology[] = []
    while (true) {
      const data = await defStoreClient.listTerminologies({ page, page_size: 100, namespace: namespaceStore.currentNamespaceParam })
      all.push(...data.items)
      if (page >= data.pages) break
      page++
    }
    terminologies.value = all.sort((a, b) => (a.label || a.value).localeCompare(b.label || b.value))
  } catch (e) {
    uiStore.showError('Failed to load terminologies', (e as Error).message)
  } finally {
    loadingTerminologies.value = false
  }
}

// -------------------------------------------------------------------------
// Term search within selected terminology
// -------------------------------------------------------------------------

async function loadInitialTerms() {
  if (!selectedTerminology.value) {
    initialTerms.value = []
    return
  }
  try {
    const data = await defStoreClient.listTerms(
      selectedTerminology.value.terminology_id,
      { page_size: 20 }
    )
    initialTerms.value = data.items
  } catch {
    initialTerms.value = []
  }
}

async function onTermSearch(event: { query: string }) {
  if (!selectedTerminology.value) {
    termSuggestions.value = []
    return
  }
  if (!event.query || event.query.length < 1) {
    // Show initial terms when field is focused/empty
    termSuggestions.value = initialTerms.value
    return
  }
  loadingSearch.value = true
  try {
    const data = await defStoreClient.listTerms(
      selectedTerminology.value.terminology_id,
      { search: event.query, page_size: 20 }
    )
    termSuggestions.value = data.items
  } catch {
    termSuggestions.value = []
  } finally {
    loadingSearch.value = false
  }
}

function onTermSelect(event: { value: Term }) {
  selectTerm(event.value)
}

function selectTerm(term: Term) {
  focusTermId.value = term.term_id
  focusTerm.value = term
  termSearch.value = null
  loadTermDocuments(term.term_id)
  // Update URL
  router.replace({
    query: {
      ...route.query,
      terminology: selectedTerminology.value?.terminology_id,
      term: term.term_id,
      depth: String(depth.value),
    },
  })
}

// -------------------------------------------------------------------------
// Graph events
// -------------------------------------------------------------------------

async function loadTermDocuments(termId: string) {
  loadingDocuments.value = true
  termDocuments.value = []
  termDocumentsTotal.value = 0
  try {
    const data = await documentStoreClient.queryDocuments({
      filters: [{ field: 'term_references.term_id', operator: 'eq', value: termId }],
      page_size: 10,
      sort_by: 'updated_at',
      sort_order: 'desc',
    })
    termDocuments.value = data.items.map(d => ({
      document_id: d.document_id,
      template_value: d.template_value || '?',
      data: d.data,
      version: d.version,
    }))
    termDocumentsTotal.value = data.total
  } catch {
    // Not critical
  } finally {
    loadingDocuments.value = false
  }
}

function onFocus(termId: string) {
  focusTermId.value = termId
  // Try to load term details
  defStoreClient.getTerm(termId).then(t => {
    focusTerm.value = t
    // Switch terminology context if the term is from a different one
    if (t.terminology_id !== selectedTerminology.value?.terminology_id) {
      const newTerm = terminologies.value.find(
        tt => tt.terminology_id === t.terminology_id
      )
      if (newTerm) selectedTerminology.value = newTerm
    }
  }).catch(() => {
    focusTerm.value = null
  })
  loadTermDocuments(termId)
  router.replace({
    query: {
      ...route.query,
      term: termId,
      depth: String(depth.value),
    },
  })
}

function onSelect(termId: string, value: string) {
  selectedNodeId.value = termId
  selectedNodeValue.value = value
}

function onTypesDiscovered(types: string[]) {
  discoveredTypes.value = types.sort()
  // On first load or when all were visible, show all
  if (visibleTypes.value.length === 0 || allTypesVisible.value) {
    visibleTypes.value = [...types]
  }
}

function getDocumentLabel(doc: TermDocument): string {
  // Try common label fields from the document data
  for (const key of ['name', 'label', 'title', 'value', 'display_name']) {
    const val = doc.data[key]
    if (val && typeof val === 'string') return val
  }
  // Fall back to first string field
  for (const val of Object.values(doc.data)) {
    if (val && typeof val === 'string' && val.length < 80) return val
  }
  return doc.document_id.slice(0, 12) + '...'
}

function toggleType(type: string) {
  const idx = visibleTypes.value.indexOf(type)
  if (idx >= 0) {
    visibleTypes.value = visibleTypes.value.filter(t => t !== type)
  } else {
    visibleTypes.value = [...visibleTypes.value, type]
  }
}

function toggleAllTypes() {
  if (allTypesVisible.value) {
    visibleTypes.value = []
  } else {
    visibleTypes.value = [...discoveredTypes.value]
  }
}

// Reload when namespace changes
watch(() => namespaceStore.currentNamespaceParam, () => {
  selectedTerminology.value = null
  focusTermId.value = null
  focusTerm.value = null
  termSuggestions.value = []
  initialTerms.value = []
  termSearch.value = null
  discoveredTypes.value = []
  visibleTypes.value = []
  loadTerminologies()
})

// -------------------------------------------------------------------------
// Init from URL query params
// -------------------------------------------------------------------------

onMounted(async () => {
  await loadTerminologies()

  // Restore state from URL
  const qTerm = route.query.term as string
  const qTerminology = route.query.terminology as string
  const qDepth = route.query.depth as string

  if (qDepth) {
    const d = parseInt(qDepth)
    if (d >= 1 && d <= 3) depth.value = d
  }

  if (qTerminology) {
    const t = terminologies.value.find(tt => tt.terminology_id === qTerminology)
    if (t) selectedTerminology.value = t
  }

  if (qTerm) {
    try {
      const term = await defStoreClient.getTerm(qTerm)
      focusTermId.value = term.term_id
      focusTerm.value = term
      loadTermDocuments(term.term_id)
      // Auto-select terminology
      if (!selectedTerminology.value) {
        const t = terminologies.value.find(
          tt => tt.terminology_id === term.terminology_id
        )
        if (t) selectedTerminology.value = t
      }
    } catch {
      // Term not found, ignore
    }
  }
})

// Reset search and load initial terms when terminology changes
watch(selectedTerminology, () => {
  termSearch.value = null
  termSuggestions.value = []
  loadInitialTerms()
})
</script>

<template>
  <div class="ontology-browser">
    <Breadcrumb :home="breadcrumbHome" :model="breadcrumbItems" />

    <div class="browser-layout">
      <!-- Left: Controls -->
      <Card class="controls-panel">
        <template #content>
          <div class="controls-sections">
            <!-- Terminology selector -->
            <div class="control-group">
              <label class="control-label">Terminology</label>
              <Select
                v-model="selectedTerminology"
                :options="terminologies"
                :option-label="(t: Terminology) => t.label || t.value"
                placeholder="Select terminology..."
                filter
                :loading="loadingTerminologies"
                class="w-full"
              />
            </div>

            <!-- Term search -->
            <div class="control-group">
              <label class="control-label">Search term</label>
              <AutoComplete
                v-model="termSearch"
                :suggestions="termSuggestions"
                :option-label="(t: Term) => t.label || t.value"
                placeholder="Type to search or browse..."
                :disabled="!selectedTerminology"
                :loading="loadingSearch"
                :dropdown="true"
                :min-length="0"
                class="w-full"
                @complete="onTermSearch"
                @item-select="onTermSelect"
              >
                <template #option="{ option }">
                  <div class="term-option">
                    <span class="term-option-label">{{ (option as Term).label || (option as Term).value }}</span>
                    <code class="term-option-code">{{ (option as Term).value }}</code>
                  </div>
                </template>
              </AutoComplete>
            </div>

            <!-- Depth -->
            <div class="control-group">
              <label class="control-label">Depth</label>
              <SelectButton
                v-model="depth"
                :options="depthOptions"
                option-label="label"
                option-value="value"
                :allow-empty="false"
              />
            </div>

            <!-- Type filter -->
            <div v-if="discoveredTypes.length > 0" class="control-group">
              <label class="control-label">
                Relationship types
                <a href="#" class="toggle-all" @click.prevent="toggleAllTypes">
                  {{ allTypesVisible ? 'none' : 'all' }}
                </a>
              </label>
              <div class="type-filters">
                <div
                  v-for="type in discoveredTypes"
                  :key="type"
                  class="type-filter-item"
                  @click="toggleType(type)"
                >
                  <Checkbox
                    :modelValue="visibleTypes.includes(type)"
                    :binary="true"
                    @update:modelValue="toggleType(type)"
                  />
                  <span
                    class="type-dot"
                    :style="{ background: TYPE_COLOURS[type] || '#95a5a6' }"
                  ></span>
                  <span class="type-label">{{ type }}</span>
                </div>
              </div>
            </div>
          </div>
        </template>
      </Card>

      <!-- Centre: Graph -->
      <div class="graph-panel">
        <div v-if="!focusTermId" class="empty-graph">
          <i class="pi pi-sitemap" style="font-size: 3rem; opacity: 0.2"></i>
          <p>Select a terminology and search for a term to begin exploring</p>
        </div>

        <EgoGraph
          v-else
          :focus-term-id="focusTermId"
          :depth="depth"
          :visible-types="visibleTypes"
          :namespace="namespaceStore.currentNamespaceParam"
          @focus="onFocus"
          @select="onSelect"
          @types-discovered="onTypesDiscovered"
        />
      </div>

      <!-- Right: Detail panel -->
      <Card class="detail-panel">
        <template #content>
          <div v-if="focusTerm" class="detail-content">
            <h3 class="detail-title">Focus Term</h3>
            <div class="detail-field">
              <span class="field-label">Value</span>
              <span class="field-value">{{ focusTerm.label || focusTerm.value }}</span>
            </div>
            <div class="detail-field">
              <span class="field-label">Code</span>
              <code class="field-value">{{ focusTerm.value }}</code>
            </div>
            <div class="detail-field">
              <span class="field-label">ID</span>
              <TruncatedId :id="focusTerm.term_id" />
            </div>
            <div class="detail-field">
              <span class="field-label">Terminology</span>
              <span class="field-value">{{ focusTerm.terminology_value || focusTerm.terminology_id }}</span>
            </div>
            <div v-if="focusTerm.description" class="detail-field">
              <span class="field-label">Description</span>
              <span class="field-value description">{{ focusTerm.description }}</span>
            </div>
            <div class="detail-field">
              <span class="field-label">Status</span>
              <Tag :value="focusTerm.status" :severity="focusTerm.status === 'active' ? 'success' : 'warn'" />
            </div>
            <Button
              label="Open Term Detail"
              icon="pi pi-external-link"
              severity="secondary"
              size="small"
              class="detail-link-btn"
              @click="router.push(`/terms/${focusTerm.term_id}`)"
            />

            <!-- Documents referencing this term -->
            <div class="documents-section">
              <h3 class="detail-title">
                Documents
                <Tag v-if="termDocumentsTotal > 0" :value="String(termDocumentsTotal)" severity="info" />
              </h3>
              <div v-if="loadingDocuments" class="documents-loading">
                <ProgressSpinner style="width: 20px; height: 20px" />
              </div>
              <div v-else-if="termDocuments.length === 0" class="documents-empty">
                No documents reference this term
              </div>
              <div v-else class="documents-list">
                <div
                  v-for="doc in termDocuments"
                  :key="doc.document_id"
                  class="document-item"
                  @click="router.push(`/documents/${doc.document_id}`)"
                >
                  <div class="doc-template">
                    <Tag :value="doc.template_value" severity="secondary" />
                  </div>
                  <div class="doc-summary">{{ getDocumentLabel(doc) }}</div>
                </div>
                <div v-if="termDocumentsTotal > termDocuments.length" class="documents-more">
                  +{{ termDocumentsTotal - termDocuments.length }} more
                </div>
              </div>
            </div>
          </div>

          <div v-else-if="selectedNodeId" class="detail-content">
            <h3 class="detail-title">Hovered Term</h3>
            <div class="detail-field">
              <span class="field-label">Value</span>
              <span class="field-value">{{ selectedNodeValue }}</span>
            </div>
            <div class="detail-field">
              <span class="field-label">ID</span>
              <TruncatedId :id="selectedNodeId" />
            </div>
            <Button
              label="Open Term Detail"
              icon="pi pi-external-link"
              severity="secondary"
              size="small"
              class="detail-link-btn"
              @click="router.push(`/terms/${selectedNodeId}`)"
            />
          </div>

          <div v-else class="detail-empty">
            <i class="pi pi-info-circle" style="font-size: 1.5rem; opacity: 0.3"></i>
            <p>Hover over a term in the graph to see its details</p>
          </div>
        </template>
      </Card>
    </div>
  </div>
</template>

<style scoped>
.ontology-browser {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  height: calc(100vh - 100px);
}

.browser-layout {
  display: grid;
  grid-template-columns: 260px 1fr 260px;
  gap: 1rem;
  flex: 1;
  min-height: 0;
}

.controls-panel {
  overflow-y: auto;
}

.controls-panel :deep(.p-card-body),
.controls-panel :deep(.p-card-content) {
  padding: 0.75rem;
}

.controls-sections {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}

.control-group {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.control-label {
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--p-text-muted-color);
  text-transform: uppercase;
  letter-spacing: 0.03em;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.toggle-all {
  font-size: 0.75rem;
  text-transform: none;
  letter-spacing: normal;
  font-weight: 500;
  color: var(--p-primary-color);
  text-decoration: none;
}
.toggle-all:hover {
  text-decoration: underline;
}

.term-option {
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
}

.term-option-label {
  font-size: 0.85rem;
  font-weight: 500;
}

.term-option-code {
  font-size: 0.7rem;
  color: var(--p-text-muted-color);
}

.type-filters {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}

.type-filter-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  cursor: pointer;
  padding: 0.2rem 0;
}

.type-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
}

.type-label {
  font-size: 0.8rem;
}

/* Graph panel */
.graph-panel {
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.empty-graph {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.75rem;
  color: var(--p-text-muted-color);
  border: 2px dashed var(--p-surface-200);
  border-radius: 6px;
}

/* Detail panel */
.detail-panel {
  overflow-y: auto;
}

.detail-panel :deep(.p-card-body),
.detail-panel :deep(.p-card-content) {
  padding: 0.75rem;
}

.detail-content {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.detail-title {
  font-size: 0.9rem;
  font-weight: 700;
  margin: 0;
  color: var(--p-text-color);
}

.detail-field {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}

.field-label {
  font-size: 0.7rem;
  font-weight: 600;
  color: var(--p-text-muted-color);
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.field-value {
  font-size: 0.85rem;
  word-break: break-all;
}

.field-value.description {
  font-size: 0.8rem;
  color: var(--p-text-muted-color);
  line-height: 1.4;
}

.detail-link-btn {
  margin-top: 0.5rem;
}

.detail-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.5rem;
  padding: 2rem 0.5rem;
  text-align: center;
  color: var(--p-text-muted-color);
  font-size: 0.85rem;
}

.w-full {
  width: 100%;
}

/* Documents section */
.documents-section {
  margin-top: 0.5rem;
  padding-top: 0.75rem;
  border-top: 1px solid var(--p-surface-200);
}

.documents-section .detail-title {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}

.documents-loading {
  display: flex;
  justify-content: center;
  padding: 0.5rem;
}

.documents-empty {
  font-size: 0.8rem;
  color: var(--p-text-muted-color);
  font-style: italic;
}

.documents-list {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}

.document-item {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  padding: 0.4rem 0.5rem;
  border-radius: 4px;
  cursor: pointer;
  border: 1px solid var(--p-surface-200);
  transition: background-color 0.15s;
}

.document-item:hover {
  background: var(--p-surface-100);
}

.doc-template {
  font-size: 0.7rem;
}

.doc-summary {
  font-size: 0.8rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.documents-more {
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
  text-align: center;
  padding: 0.25rem;
}
</style>
