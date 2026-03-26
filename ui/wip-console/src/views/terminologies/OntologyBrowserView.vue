<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import Breadcrumb from 'primevue/breadcrumb'
import Card from 'primevue/card'
import Select from 'primevue/select'
import InputText from 'primevue/inputtext'
import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import Tag from 'primevue/tag'
import SelectButton from 'primevue/selectbutton'
import ProgressSpinner from 'primevue/progressspinner'
import { useUiStore } from '@/stores'
import { defStoreClient } from '@/api/client'
import type { Terminology, Term } from '@/types'
import TruncatedId from '@/components/common/TruncatedId.vue'
import EgoGraph from '@/components/terminologies/EgoGraph.vue'

const router = useRouter()
const route = useRoute()
const uiStore = useUiStore()

// -------------------------------------------------------------------------
// State
// -------------------------------------------------------------------------

const terminologies = ref<Terminology[]>([])
const selectedTerminology = ref<Terminology | null>(null)
const loadingTerminologies = ref(false)

const termSearch = ref('')
const searchResults = ref<Term[]>([])
const loadingSearch = ref(false)
let searchTimeout: ReturnType<typeof setTimeout> | null = null

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
      const data = await defStoreClient.listTerminologies({ page, page_size: 100 })
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

function onSearchInput() {
  if (searchTimeout) clearTimeout(searchTimeout)
  if (!selectedTerminology.value || termSearch.value.length < 2) {
    searchResults.value = []
    return
  }
  searchTimeout = setTimeout(() => searchTerms(), 300)
}

async function searchTerms() {
  if (!selectedTerminology.value) return
  loadingSearch.value = true
  try {
    const data = await defStoreClient.listTerms(
      selectedTerminology.value.terminology_id,
      { search: termSearch.value, page_size: 20 }
    )
    searchResults.value = data.items
  } catch (e) {
    searchResults.value = []
  } finally {
    loadingSearch.value = false
  }
}

function selectTerm(term: Term) {
  focusTermId.value = term.term_id
  focusTerm.value = term
  searchResults.value = []
  termSearch.value = ''
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

// Reset search when terminology changes
watch(selectedTerminology, () => {
  termSearch.value = ''
  searchResults.value = []
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
              <div class="search-wrapper">
                <InputText
                  v-model="termSearch"
                  placeholder="Type to search..."
                  :disabled="!selectedTerminology"
                  class="w-full"
                  @input="onSearchInput"
                />
                <ProgressSpinner
                  v-if="loadingSearch"
                  style="width: 18px; height: 18px; position: absolute; right: 8px; top: 8px"
                />
              </div>
              <div v-if="searchResults.length > 0" class="search-results">
                <div
                  v-for="term in searchResults"
                  :key="term.term_id"
                  class="search-result-item"
                  @click="selectTerm(term)"
                >
                  <span class="result-value">{{ term.label || term.value }}</span>
                  <code class="result-id">{{ term.value }}</code>
                </div>
              </div>
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

.search-wrapper {
  position: relative;
}

.search-results {
  border: 1px solid var(--p-surface-200);
  border-radius: 6px;
  max-height: 200px;
  overflow-y: auto;
  background: var(--p-surface-0);
}

.search-result-item {
  padding: 0.5rem 0.75rem;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
  border-bottom: 1px solid var(--p-surface-100);
}

.search-result-item:hover {
  background: var(--p-surface-50);
}

.search-result-item:last-child {
  border-bottom: none;
}

.result-value {
  font-size: 0.85rem;
  font-weight: 500;
}

.result-id {
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
</style>
