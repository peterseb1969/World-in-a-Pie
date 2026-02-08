<script setup lang="ts">
import { ref, onMounted, watch, computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import Card from 'primevue/card'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Tag from 'primevue/tag'
import ProgressSpinner from 'primevue/progressspinner'
import Message from 'primevue/message'
import { useAuthStore, useUiStore } from '@/stores'
import {
  reportingSyncClient,
  type SearchResult,
  type EntityDetails,
  type EntityReference,
  type IncomingReference
} from '@/api/client'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()
const uiStore = useUiStore()

// Search state
const searchQuery = ref((route.query.q as string) || '')
const searching = ref(false)
const searchResults = ref<SearchResult[]>([])
const searchCounts = ref<Record<string, number>>({})
const hasSearched = ref(false)

// Entity inspection state
const inspectedEntity = ref<EntityDetails | null>(null)
const inspectionLoading = ref(false)
const inspectionError = ref<string | null>(null)

// Referenced by state
const referencedBy = ref<IncomingReference[]>([])
const referencedByTotal = ref(0)
const referencedByLoading = ref(false)

// Check if we have entity params from URL
const hasEntityParams = computed(() => {
  return route.query.type && route.query.id
})

async function performSearch() {
  if (!searchQuery.value.trim()) {
    searchResults.value = []
    searchCounts.value = {}
    hasSearched.value = false
    return
  }

  searching.value = true
  hasSearched.value = true

  try {
    const response = await reportingSyncClient.search({
      query: searchQuery.value.trim(),
      limit: 50
    })
    searchResults.value = response.results
    searchCounts.value = response.counts
  } catch (error) {
    console.error('Search failed:', error)
    uiStore.showError('Search failed', error instanceof Error ? error.message : 'Unknown error')
    searchResults.value = []
    searchCounts.value = {}
  } finally {
    searching.value = false
  }
}

async function inspectEntity(type: string, id: string) {
  inspectionLoading.value = true
  inspectionError.value = null
  inspectedEntity.value = null
  referencedBy.value = []
  referencedByTotal.value = 0

  const entityType = type as 'document' | 'template' | 'terminology' | 'term'

  try {
    // Fetch entity references (outgoing)
    const response = await reportingSyncClient.getEntityReferences(entityType, id)

    if (response.error) {
      inspectionError.value = response.error
    } else {
      inspectedEntity.value = response.entity
    }
  } catch (error) {
    console.error('Entity inspection failed:', error)
    inspectionError.value = error instanceof Error ? error.message : 'Failed to load entity'
  } finally {
    inspectionLoading.value = false
  }

  // Fetch referenced by (incoming) - only for non-document entities
  if (entityType !== 'document') {
    referencedByLoading.value = true
    try {
      const refByResponse = await reportingSyncClient.getReferencedBy(entityType, id)
      if (!refByResponse.error) {
        referencedBy.value = refByResponse.referenced_by
        referencedByTotal.value = refByResponse.total
      }
    } catch (error) {
      console.error('Referenced-by lookup failed:', error)
    } finally {
      referencedByLoading.value = false
    }
  }
}

function selectSearchResult(result: SearchResult) {
  // Update URL and inspect the entity
  router.replace({
    path: '/audit/explorer',
    query: { type: result.type, id: result.id }
  })
  inspectEntity(result.type, result.id)
}

function closeInspection() {
  inspectedEntity.value = null
  inspectionError.value = null
  router.replace({ path: '/audit/explorer', query: searchQuery.value ? { q: searchQuery.value } : {} })
}

function getTypeSeverity(type: string): 'success' | 'info' | 'warn' | 'danger' | 'secondary' {
  switch (type) {
    case 'terminology': return 'info'
    case 'term': return 'secondary'
    case 'template': return 'success'
    case 'document': return 'warn'
    default: return 'secondary'
  }
}

function getStatusSeverity(status: string | null): 'success' | 'warn' | 'danger' | 'secondary' {
  switch (status) {
    case 'active': return 'success'
    case 'deprecated': return 'warn'
    case 'inactive': return 'danger'
    default: return 'secondary'
  }
}

function getRefStatusSeverity(status: string): 'success' | 'warn' | 'danger' {
  switch (status) {
    case 'valid': return 'success'
    case 'inactive': return 'warn'
    case 'broken': return 'danger'
    default: return 'danger'
  }
}

function getRefStatusIcon(status: string): string {
  switch (status) {
    case 'valid': return 'pi pi-check-circle'
    case 'inactive': return 'pi pi-exclamation-triangle'
    case 'broken': return 'pi pi-times-circle'
    default: return 'pi pi-question-circle'
  }
}

function formatDate(dateString: string | null): string {
  if (!dateString) return '-'
  return new Date(dateString).toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit'
  })
}

function navigateToReference(ref: EntityReference) {
  // Navigate to inspect the referenced entity
  router.push({
    path: '/audit/explorer',
    query: { type: ref.ref_type, id: ref.ref_id }
  })
}

function navigateToIncomingRef(ref: IncomingReference) {
  // Navigate to inspect the referencing entity
  router.push({
    path: '/audit/explorer',
    query: { type: ref.entity_type, id: ref.entity_id }
  })
}

function getRefTypeLabel(refType: string): string {
  switch (refType) {
    case 'uses_template': return 'uses'
    case 'extends': return 'extends'
    case 'template_ref': return 'references'
    case 'terminology_ref': return 'references'
    case 'term_ref': return 'references'
    default: return refType
  }
}

// Debounced search on input
let searchTimeout: ReturnType<typeof setTimeout> | null = null
function onSearchInput() {
  if (searchTimeout) {
    clearTimeout(searchTimeout)
  }
  searchTimeout = setTimeout(() => {
    performSearch()
  }, 300)
}

onMounted(() => {
  // If entity params present, inspect that entity
  if (hasEntityParams.value) {
    inspectEntity(route.query.type as string, route.query.id as string)
  }
  // If search query present, perform search
  else if (searchQuery.value.trim() && authStore.isAuthenticated) {
    performSearch()
  }
})

// Watch for route query changes
watch(
  () => route.query,
  (newQuery) => {
    // Handle entity inspection
    if (newQuery.type && newQuery.id) {
      inspectEntity(newQuery.type as string, newQuery.id as string)
    }
    // Handle search
    else if (newQuery.q && typeof newQuery.q === 'string') {
      searchQuery.value = newQuery.q
      if (authStore.isAuthenticated) {
        performSearch()
      }
    }
  }
)

watch(
  () => authStore.isAuthenticated,
  (isAuth, wasAuth) => {
    if (isAuth && !wasAuth) {
      if (hasEntityParams.value) {
        inspectEntity(route.query.type as string, route.query.id as string)
      } else if (searchQuery.value.trim()) {
        performSearch()
      }
    }
  }
)
</script>

<template>
  <div class="audit-explorer">
    <div class="page-header">
      <h1>Entity Explorer</h1>
      <p class="subtitle">Search and inspect entities across all services</p>
    </div>

    <!-- Auth warning -->
    <div v-if="!authStore.isAuthenticated" class="auth-warning">
      <Card>
        <template #content>
          <div class="warning-content">
            <i class="pi pi-exclamation-triangle"></i>
            <div>
              <h3>Authentication Required</h3>
              <p>Please log in to use the entity explorer.</p>
            </div>
          </div>
        </template>
      </Card>
    </div>

    <template v-else>
      <!-- Entity Inspection Panel -->
      <Card v-if="inspectedEntity || inspectionLoading || inspectionError" class="inspection-card">
        <template #title>
          <div class="inspection-header">
            <div class="card-title">
              <i class="pi pi-info-circle"></i>
              <span>Entity Inspector</span>
            </div>
            <Button
              icon="pi pi-times"
              text
              rounded
              size="small"
              @click="closeInspection"
              v-tooltip.left="'Close'"
            />
          </div>
        </template>
        <template #content>
          <!-- Loading -->
          <div v-if="inspectionLoading" class="loading-state">
            <ProgressSpinner style="width: 30px; height: 30px" />
            <span>Loading entity details...</span>
          </div>

          <!-- Error -->
          <Message v-else-if="inspectionError" severity="error" :closable="false">
            {{ inspectionError }}
          </Message>

          <!-- Entity Details -->
          <div v-else-if="inspectedEntity" class="entity-details">
            <!-- Entity Header -->
            <div class="entity-header">
              <div class="entity-info">
                <Tag :severity="getTypeSeverity(inspectedEntity.entity_type)" class="type-badge">
                  {{ inspectedEntity.entity_type }}
                </Tag>
                <h3 class="entity-title">
                  {{ inspectedEntity.entity_name || inspectedEntity.entity_code || inspectedEntity.entity_id }}
                </h3>
                <Tag
                  v-if="inspectedEntity.entity_status"
                  :severity="getStatusSeverity(inspectedEntity.entity_status)"
                  size="small"
                >
                  {{ inspectedEntity.entity_status }}
                </Tag>
              </div>
              <div class="entity-meta">
                <span v-if="inspectedEntity.entity_code" class="meta-item">
                  <strong>Code:</strong> {{ inspectedEntity.entity_code }}
                </span>
                <span class="meta-item">
                  <strong>ID:</strong> <code>{{ inspectedEntity.entity_id }}</code>
                </span>
                <span v-if="inspectedEntity.version" class="meta-item">
                  <strong>Version:</strong> {{ inspectedEntity.version }}
                </span>
                <span v-if="inspectedEntity.updated_at" class="meta-item">
                  <strong>Updated:</strong> {{ formatDate(inspectedEntity.updated_at) }}
                </span>
              </div>
            </div>

            <!-- Reference Summary -->
            <div class="reference-summary">
              <div class="summary-item valid">
                <i class="pi pi-check-circle"></i>
                <span>{{ inspectedEntity.valid_refs }} valid</span>
              </div>
              <div v-if="inspectedEntity.broken_refs > 0" class="summary-item broken">
                <i class="pi pi-times-circle"></i>
                <span>{{ inspectedEntity.broken_refs }} broken</span>
              </div>
              <div v-if="inspectedEntity.inactive_refs > 0" class="summary-item inactive">
                <i class="pi pi-exclamation-triangle"></i>
                <span>{{ inspectedEntity.inactive_refs }} inactive</span>
              </div>
            </div>

            <!-- References List -->
            <div v-if="inspectedEntity.references.length > 0" class="references-section">
              <h4>References</h4>
              <div class="references-list">
                <div
                  v-for="(ref, index) in inspectedEntity.references"
                  :key="index"
                  class="reference-item"
                  :class="ref.status"
                  @click="navigateToReference(ref)"
                >
                  <div class="ref-icon">
                    <i :class="getRefStatusIcon(ref.status)"></i>
                  </div>
                  <div class="ref-content">
                    <div class="ref-main">
                      <Tag :severity="getTypeSeverity(ref.ref_type)" size="small">
                        {{ ref.ref_type }}
                      </Tag>
                      <span class="ref-name">
                        {{ ref.ref_name || ref.ref_code || ref.ref_id }}
                      </span>
                      <Tag :severity="getRefStatusSeverity(ref.status)" size="small">
                        {{ ref.status }}
                      </Tag>
                    </div>
                    <div class="ref-details">
                      <code v-if="ref.field_path" class="field-path">{{ ref.field_path }}</code>
                      <span v-if="ref.ref_code && ref.ref_name" class="ref-code">{{ ref.ref_code }}</span>
                      <span class="ref-id">{{ ref.ref_id }}</span>
                    </div>
                    <div v-if="ref.error" class="ref-error">
                      <i class="pi pi-exclamation-circle"></i>
                      {{ ref.error }}
                    </div>
                  </div>
                  <div class="ref-action">
                    <i class="pi pi-chevron-right"></i>
                  </div>
                </div>
              </div>
            </div>

            <!-- No references -->
            <div v-else class="no-references">
              <i class="pi pi-info-circle"></i>
              <span>This entity has no outgoing references.</span>
            </div>

            <!-- Referenced By Section -->
            <div v-if="inspectedEntity.entity_type !== 'document'" class="referenced-by-section">
              <h4>
                Referenced By
                <span v-if="referencedByTotal > 0" class="ref-count">({{ referencedByTotal }})</span>
              </h4>

              <!-- Loading -->
              <div v-if="referencedByLoading" class="loading-inline">
                <ProgressSpinner style="width: 20px; height: 20px" />
                <span>Loading...</span>
              </div>

              <!-- References list -->
              <div v-else-if="referencedBy.length > 0" class="references-list">
                <div
                  v-for="(ref, index) in referencedBy"
                  :key="index"
                  class="reference-item valid"
                  @click="navigateToIncomingRef(ref)"
                >
                  <div class="ref-icon">
                    <i class="pi pi-arrow-left"></i>
                  </div>
                  <div class="ref-content">
                    <div class="ref-main">
                      <Tag :severity="getTypeSeverity(ref.entity_type)" size="small">
                        {{ ref.entity_type }}
                      </Tag>
                      <span class="ref-name">
                        {{ ref.entity_name || ref.entity_code || ref.entity_id }}
                      </span>
                      <Tag
                        v-if="ref.entity_status"
                        :severity="getStatusSeverity(ref.entity_status)"
                        size="small"
                      >
                        {{ ref.entity_status }}
                      </Tag>
                    </div>
                    <div class="ref-details">
                      <span class="ref-type-label">{{ getRefTypeLabel(ref.reference_type) }}</span>
                      <code v-if="ref.field_path" class="field-path">{{ ref.field_path }}</code>
                      <span v-if="ref.entity_code" class="ref-code">{{ ref.entity_code }}</span>
                      <span class="ref-id">{{ ref.entity_id }}</span>
                    </div>
                  </div>
                  <div class="ref-action">
                    <i class="pi pi-chevron-right"></i>
                  </div>
                </div>
              </div>

              <!-- No incoming references -->
              <div v-else class="no-references">
                <i class="pi pi-info-circle"></i>
                <span>No other entities reference this {{ inspectedEntity.entity_type }}.</span>
              </div>
            </div>
          </div>
        </template>
      </Card>

      <!-- Search Box -->
      <Card class="search-card">
        <template #content>
          <div class="search-box">
            <span class="p-input-icon-left p-input-icon-right search-input-wrapper">
              <i class="pi pi-search" />
              <InputText
                v-model="searchQuery"
                placeholder="Search terminologies, terms, templates, documents..."
                class="search-input"
                @input="onSearchInput"
                @keyup.enter="performSearch"
              />
              <i v-if="searching" class="pi pi-spin pi-spinner" />
            </span>
            <Button
              label="Search"
              icon="pi pi-search"
              :loading="searching"
              @click="performSearch"
            />
          </div>

          <!-- Search counts -->
          <div v-if="hasSearched && !searching" class="search-counts">
            <span class="count-label">Found:</span>
            <Tag
              v-for="(count, type) in searchCounts"
              :key="type"
              :severity="getTypeSeverity(type as string)"
              class="count-tag"
            >
              {{ count }} {{ type }}{{ count !== 1 ? 's' : '' }}
            </Tag>
            <span v-if="Object.keys(searchCounts).length === 0" class="no-results">
              No results
            </span>
          </div>
        </template>
      </Card>

      <!-- Results -->
      <Card v-if="hasSearched" class="results-card">
        <template #title>
          <div class="card-title">
            <i class="pi pi-list"></i>
            <span>Search Results</span>
          </div>
        </template>
        <template #content>
          <div v-if="searching" class="loading-state">
            <ProgressSpinner style="width: 30px; height: 30px" />
            <span>Searching...</span>
          </div>

          <DataTable
            v-else
            :value="searchResults"
            :paginator="searchResults.length > 20"
            :rows="20"
            size="small"
            @row-click="(e) => selectSearchResult(e.data)"
            class="results-table"
            :pt="{ bodyRow: { style: 'cursor: pointer' } }"
          >
            <Column header="Type" style="width: 120px">
              <template #body="{ data }">
                <Tag :severity="getTypeSeverity(data.type)" size="small">
                  {{ data.type }}
                </Tag>
              </template>
            </Column>
            <Column header="Name / Value">
              <template #body="{ data }">
                <div class="entity-cell">
                  <span class="entity-name">{{ data.name || data.id }}</span>
                  <span v-if="data.code" class="entity-code">{{ data.code }}</span>
                </div>
              </template>
            </Column>
            <Column header="Description">
              <template #body="{ data }">
                <span class="description">{{ data.description || '-' }}</span>
              </template>
            </Column>
            <Column header="Status" style="width: 100px">
              <template #body="{ data }">
                <Tag v-if="data.status" :severity="getStatusSeverity(data.status)" size="small">
                  {{ data.status }}
                </Tag>
                <span v-else class="na">-</span>
              </template>
            </Column>
            <Column style="width: 50px">
              <template #body>
                <i class="pi pi-chevron-right inspect-icon"></i>
              </template>
            </Column>
            <template #empty>
              <div class="empty-state">
                <i class="pi pi-search"></i>
                <p>No results found for "{{ searchQuery }}"</p>
              </div>
            </template>
          </DataTable>
        </template>
      </Card>

      <!-- Initial state (no search, no inspection) -->
      <Card v-else-if="!inspectedEntity && !inspectionLoading" class="initial-card">
        <template #content>
          <div class="initial-state">
            <i class="pi pi-search"></i>
            <h3>Search for Entities</h3>
            <p>Enter a search term to find terminologies, terms, templates, or documents.</p>
            <div class="search-tips">
              <h4>Search Tips</h4>
              <ul>
                <li>Search by code: <code>PERSON</code>, <code>T-000001</code></li>
                <li>Search by name: <code>Salutation</code>, <code>Country</code></li>
                <li>Search by ID: <code>TPL-000001</code></li>
              </ul>
            </div>
          </div>
        </template>
      </Card>
    </template>
  </div>
</template>

<style scoped>
.audit-explorer {
  max-width: 1200px;
  margin: 0 auto;
}

.page-header {
  margin-bottom: 2rem;
}

.page-header h1 {
  font-size: 1.75rem;
  font-weight: 600;
  margin-bottom: 0.25rem;
}

.subtitle {
  color: var(--p-text-muted-color);
}

/* Auth warning */
.auth-warning {
  margin-bottom: 2rem;
}

.warning-content {
  display: flex;
  align-items: flex-start;
  gap: 1rem;
}

.warning-content i {
  font-size: 2rem;
  color: var(--p-orange-500);
}

.warning-content h3 {
  margin: 0 0 0.25rem 0;
  font-size: 1.125rem;
}

.warning-content p {
  margin: 0;
  color: var(--p-text-muted-color);
}

/* Inspection card */
.inspection-card {
  margin-bottom: 1.5rem;
  border-left: 4px solid var(--p-primary-color);
}

.inspection-card :deep(.p-card-title) {
  font-size: 1rem;
}

.inspection-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.card-title {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.card-title i {
  color: var(--p-primary-color);
}

.loading-state {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 1rem;
  color: var(--p-text-muted-color);
}

/* Entity details */
.entity-details {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.entity-header {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.entity-info {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  flex-wrap: wrap;
}

.type-badge {
  text-transform: capitalize;
}

.entity-title {
  margin: 0;
  font-size: 1.25rem;
  font-weight: 600;
}

.entity-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 1rem;
  font-size: 0.875rem;
  color: var(--p-text-muted-color);
}

.meta-item code {
  background: var(--p-surface-100);
  padding: 0.125rem 0.375rem;
  border-radius: 4px;
  font-size: 0.75rem;
}

/* Reference summary */
.reference-summary {
  display: flex;
  gap: 1.5rem;
  padding: 1rem;
  background: var(--p-surface-50);
  border-radius: 8px;
}

.summary-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-weight: 500;
}

.summary-item.valid {
  color: var(--p-green-600);
}

.summary-item.broken {
  color: var(--p-red-600);
}

.summary-item.inactive {
  color: var(--p-orange-600);
}

/* References section */
.references-section h4 {
  margin: 0 0 0.75rem 0;
  font-size: 0.875rem;
  text-transform: uppercase;
  color: var(--p-text-muted-color);
  letter-spacing: 0.05em;
}

.references-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.reference-item {
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
  padding: 0.75rem;
  background: var(--p-surface-50);
  border-radius: 8px;
  cursor: pointer;
  transition: background-color 0.15s;
  border-left: 3px solid transparent;
}

.reference-item:hover {
  background: var(--p-surface-100);
}

.reference-item.valid {
  border-left-color: var(--p-green-500);
}

.reference-item.broken {
  border-left-color: var(--p-red-500);
  background: var(--p-red-50);
}

.reference-item.broken:hover {
  background: var(--p-red-100);
}

.reference-item.inactive {
  border-left-color: var(--p-orange-500);
  background: var(--p-orange-50);
}

.reference-item.inactive:hover {
  background: var(--p-orange-100);
}

.ref-icon {
  padding-top: 0.125rem;
}

.ref-icon i {
  font-size: 1.25rem;
}

.reference-item.valid .ref-icon i {
  color: var(--p-green-500);
}

.reference-item.broken .ref-icon i {
  color: var(--p-red-500);
}

.reference-item.inactive .ref-icon i {
  color: var(--p-orange-500);
}

.ref-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.ref-main {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
}

.ref-name {
  font-weight: 500;
}

.ref-details {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
}

.field-path {
  background: var(--p-surface-100);
  padding: 0.125rem 0.375rem;
  border-radius: 4px;
}

.ref-id {
  font-family: monospace;
}

.ref-error {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  font-size: 0.8125rem;
  color: var(--p-red-600);
  margin-top: 0.25rem;
}

.ref-action {
  padding-top: 0.125rem;
  color: var(--p-text-muted-color);
}

.no-references {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 1rem;
  background: var(--p-surface-50);
  border-radius: 8px;
  color: var(--p-text-muted-color);
}

/* Referenced By section */
.referenced-by-section {
  border-top: 1px solid var(--p-surface-200);
  padding-top: 1.5rem;
}

.referenced-by-section h4 {
  margin: 0 0 0.75rem 0;
  font-size: 0.875rem;
  text-transform: uppercase;
  color: var(--p-text-muted-color);
  letter-spacing: 0.05em;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.ref-count {
  font-weight: normal;
  color: var(--p-primary-color);
}

.loading-inline {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem;
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
}

.ref-type-label {
  font-style: italic;
  color: var(--p-text-muted-color);
}

/* Search card */
.search-card {
  margin-bottom: 1.5rem;
}

.search-box {
  display: flex;
  gap: 1rem;
  align-items: center;
}

.search-input-wrapper {
  flex: 1;
}

.search-input {
  width: 100%;
}

.search-counts {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-top: 1rem;
  flex-wrap: wrap;
}

.count-label {
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
}

.count-tag {
  font-size: 0.75rem;
}

.no-results {
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
}

/* Results card */
.results-card :deep(.p-card-title) {
  font-size: 1rem;
}

.results-card :deep(.p-card-content) {
  padding: 0;
}

.results-table :deep(.p-datatable-tbody > tr:hover) {
  background-color: var(--p-surface-100);
}

.entity-cell {
  display: flex;
  flex-direction: column;
}

.entity-name {
  font-weight: 500;
}

.entity-code {
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
  font-family: monospace;
}

.description {
  font-size: 0.875rem;
  color: var(--p-text-muted-color);
}

.na {
  color: var(--p-surface-400);
}

.inspect-icon {
  color: var(--p-text-muted-color);
}

.empty-state {
  text-align: center;
  padding: 3rem 1rem;
  color: var(--p-text-muted-color);
}

.empty-state i {
  font-size: 3rem;
  margin-bottom: 1rem;
  display: block;
}

.empty-state p {
  margin: 0;
}

/* Initial state */
.initial-card :deep(.p-card-content) {
  padding: 2rem;
}

.initial-state {
  text-align: center;
  max-width: 500px;
  margin: 0 auto;
}

.initial-state i {
  font-size: 4rem;
  color: var(--p-primary-200);
  margin-bottom: 1rem;
}

.initial-state h3 {
  margin: 0 0 0.5rem 0;
  font-size: 1.25rem;
}

.initial-state > p {
  color: var(--p-text-muted-color);
  margin: 0 0 2rem 0;
}

.search-tips {
  text-align: left;
  background: var(--p-surface-100);
  padding: 1rem;
  border-radius: 6px;
}

.search-tips h4 {
  margin: 0 0 0.5rem 0;
  font-size: 0.875rem;
  color: var(--p-text-muted-color);
}

.search-tips ul {
  margin: 0;
  padding-left: 1.25rem;
  font-size: 0.875rem;
}

.search-tips li {
  margin-bottom: 0.25rem;
}

.search-tips code {
  background: var(--p-surface-0);
  padding: 0.125rem 0.375rem;
  border-radius: 4px;
  font-size: 0.8125rem;
}

@media (max-width: 768px) {
  .search-box {
    flex-direction: column;
  }

  .search-input-wrapper {
    width: 100%;
  }

  .reference-summary {
    flex-direction: column;
    gap: 0.75rem;
  }
}
</style>
