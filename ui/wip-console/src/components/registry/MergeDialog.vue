<script setup lang="ts">
import { ref, computed } from 'vue'
import Dialog from 'primevue/dialog'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Tag from 'primevue/tag'
import Steps from 'primevue/steps'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import { registryClient } from '@/api/client'
import { useUiStore } from '@/stores'
import type { RegistrySearchResult, RegistryEntryFull } from '@/types'
import CompositeKeyDisplay from './CompositeKeyDisplay.vue'

const props = defineProps<{
  visible: boolean
  preferredEntry: RegistryEntryFull
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  merged: []
}>()

const uiStore = useUiStore()

// Step state
const activeStep = ref(0)
const steps = [
  { label: 'Search' },
  { label: 'Preview' },
  { label: 'Confirm' },
]

// Search state
const searchQuery = ref('')
const searching = ref(false)
const searchResults = ref<RegistrySearchResult[]>([])
let searchTimeout: ReturnType<typeof setTimeout> | null = null

// Selected deprecated entry
const selectedResult = ref<RegistrySearchResult | null>(null)
const deprecatedDetail = ref<RegistryEntryFull | null>(null)
const loadingDetail = ref(false)

// Merge state
const merging = ref(false)
const mergeResult = ref<{ status: string; error?: string } | null>(null)

function closeDialog() {
  emit('update:visible', false)
  resetState()
}

function resetState() {
  activeStep.value = 0
  searchQuery.value = ''
  searchResults.value = []
  selectedResult.value = null
  deprecatedDetail.value = null
  mergeResult.value = null
}

function onSearchInput() {
  if (searchTimeout) clearTimeout(searchTimeout)
  searchTimeout = setTimeout(doSearch, 300)
}

async function doSearch() {
  const q = searchQuery.value.trim()
  if (q.length < 2) {
    searchResults.value = []
    return
  }

  searching.value = true
  try {
    const response = await registryClient.unifiedSearch({ q, page_size: 10 })
    // Filter out the preferred entry itself
    searchResults.value = response.items.filter(
      item => item.entry_id !== props.preferredEntry.entry_id && item.status === 'active'
    )
  } catch {
    searchResults.value = []
  } finally {
    searching.value = false
  }
}

async function selectForMerge(result: RegistrySearchResult) {
  selectedResult.value = result
  loadingDetail.value = true
  try {
    deprecatedDetail.value = await registryClient.getEntry(result.entry_id)
    activeStep.value = 1
  } catch (e) {
    uiStore.showError('Failed to load entry details', (e as Error).message)
  } finally {
    loadingDetail.value = false
  }
}

function goBack() {
  if (activeStep.value > 0) {
    activeStep.value--
    if (activeStep.value === 0) {
      selectedResult.value = null
      deprecatedDetail.value = null
    }
  }
}

function proceedToConfirm() {
  activeStep.value = 2
}

async function executeMerge() {
  merging.value = true
  try {
    const result = await registryClient.mergeEntries({
      preferred_id: props.preferredEntry.entry_id,
      deprecated_id: selectedResult.value!.entry_id,
    })
    mergeResult.value = result
    if (result.status === 'merged') {
      uiStore.showSuccess(
        `Merged ${selectedResult.value!.entry_id} into ${props.preferredEntry.entry_id}`
      )
      emit('merged')
      closeDialog()
    } else {
      uiStore.showError('Merge failed', result.error || result.status)
    }
  } catch (e) {
    uiStore.showError('Merge failed', (e as Error).message)
  } finally {
    merging.value = false
  }
}

const synonymsToTransfer = computed(() => {
  if (!deprecatedDetail.value) return 0
  return deprecatedDetail.value.synonyms.length + 1 // +1 for the deprecated entry's own primary key
})
</script>

<template>
  <Dialog
    :visible="visible"
    @update:visible="emit('update:visible', $event)"
    header="Merge Entries"
    :modal="true"
    :style="{ width: '700px' }"
  >
    <Steps :model="steps" :activeStep="activeStep" class="merge-steps" />

    <!-- Step 0: Search -->
    <div v-if="activeStep === 0" class="step-content">
      <p class="step-description">
        Search for the entry to deprecate and merge into
        <strong>{{ preferredEntry.entry_id }}</strong>.
      </p>

      <div class="search-box">
        <span class="p-input-icon-left search-input">
          <i class="pi pi-search" />
          <InputText
            v-model="searchQuery"
            placeholder="Search by ID, key value, or synonym..."
            @input="onSearchInput"
            class="w-full"
          />
        </span>
      </div>

      <div v-if="searching" class="loading-inline">
        <i class="pi pi-spin pi-spinner"></i> Searching...
      </div>

      <DataTable
        v-else-if="searchResults.length > 0"
        :value="searchResults"
        size="small"
        stripedRows
        selectionMode="single"
        @rowSelect="(e: any) => selectForMerge(e.data)"
        class="search-results-table"
      >
        <Column header="Entry ID" style="width: 180px">
          <template #body="{ data }">
            <code>{{ data.entry_id }}</code>
          </template>
        </Column>
        <Column header="Namespace" style="width: 110px">
          <template #body="{ data }">
            <code class="namespace-badge">{{ data.namespace }}</code>
          </template>
        </Column>
        <Column header="Type" style="width: 100px">
          <template #body="{ data }">
            {{ data.entity_type }}
          </template>
        </Column>
        <Column header="Matched Via">
          <template #body="{ data }">
            <span class="resolution-text">{{ data.resolution_path }}</span>
          </template>
        </Column>
        <Column header="" style="width: 70px">
          <template #body="{ data }">
            <Button
              label="Select"
              size="small"
              text
              @click="selectForMerge(data)"
            />
          </template>
        </Column>
      </DataTable>

      <div v-else-if="searchQuery.length >= 2 && !searching" class="empty-results">
        No matching entries found.
      </div>
    </div>

    <!-- Step 1: Preview -->
    <div v-if="activeStep === 1 && deprecatedDetail" class="step-content">
      <p class="step-description">Preview the merge operation:</p>

      <div class="merge-preview">
        <div class="preview-card preferred">
          <div class="preview-label">Preferred (keeps)</div>
          <code class="preview-id">{{ preferredEntry.entry_id }}</code>
          <div class="preview-meta">
            <Tag :value="preferredEntry.namespace" severity="info" />
            <span>{{ preferredEntry.entity_type }}</span>
          </div>
          <CompositeKeyDisplay :compositeKey="preferredEntry.primary_composite_key" compact />
        </div>

        <div class="merge-arrow">
          <i class="pi pi-arrow-left"></i>
          <span class="merge-arrow-label">absorbs</span>
        </div>

        <div class="preview-card deprecated">
          <div class="preview-label">Deprecated (deactivated)</div>
          <code class="preview-id">{{ deprecatedDetail.entry_id }}</code>
          <div class="preview-meta">
            <Tag :value="deprecatedDetail.namespace" severity="warn" />
            <span>{{ deprecatedDetail.entity_type }}</span>
          </div>
          <CompositeKeyDisplay :compositeKey="deprecatedDetail.primary_composite_key" compact />
        </div>
      </div>

      <div class="merge-summary">
        <h4>What will happen:</h4>
        <ul>
          <li>{{ synonymsToTransfer }} synonym(s) will be transferred to the preferred entry</li>
          <li>{{ deprecatedDetail.additional_ids.length }} additional ID(s) will be transferred</li>
          <li>{{ deprecatedDetail.entry_id }} will become <Tag value="inactive" severity="danger" /></li>
          <li>All lookups for {{ deprecatedDetail.entry_id }} will resolve to {{ preferredEntry.entry_id }}</li>
        </ul>
      </div>
    </div>

    <!-- Step 2: Confirm -->
    <div v-if="activeStep === 2" class="step-content">
      <div class="confirm-box">
        <i class="pi pi-exclamation-triangle confirm-icon"></i>
        <p>
          This will permanently merge <strong>{{ deprecatedDetail?.entry_id }}</strong>
          into <strong>{{ preferredEntry.entry_id }}</strong>.
          The deprecated entry will be deactivated.
        </p>
        <p class="confirm-note">This action cannot be undone.</p>
      </div>
    </div>

    <template #footer>
      <Button
        v-if="activeStep > 0"
        label="Back"
        severity="secondary"
        text
        icon="pi pi-arrow-left"
        @click="goBack"
      />
      <Button
        label="Cancel"
        severity="secondary"
        text
        @click="closeDialog"
      />
      <Button
        v-if="activeStep === 1"
        label="Continue"
        icon="pi pi-arrow-right"
        @click="proceedToConfirm"
      />
      <Button
        v-if="activeStep === 2"
        label="Merge Entries"
        icon="pi pi-check"
        severity="danger"
        :loading="merging"
        @click="executeMerge"
      />
    </template>
  </Dialog>
</template>

<style scoped>
.merge-steps {
  margin-bottom: 1.25rem;
}

.step-content {
  min-height: 200px;
}

.step-description {
  margin: 0 0 1rem;
  font-size: 0.875rem;
  color: var(--p-text-muted-color);
}

.search-box {
  margin-bottom: 1rem;
}

.search-input {
  position: relative;
  width: 100%;
}

.search-input > i {
  position: absolute;
  left: 0.75rem;
  top: 50%;
  transform: translateY(-50%);
  color: var(--p-text-muted-color);
}

.search-input > input {
  padding-left: 2.5rem;
}

.w-full {
  width: 100%;
}

.loading-inline {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 1rem;
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
}

.namespace-badge {
  font-size: 0.75rem;
  background: var(--p-surface-100);
  padding: 0.125rem 0.375rem;
  border-radius: 3px;
}

.resolution-text {
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
}

.empty-results {
  text-align: center;
  padding: 2rem;
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
}

.merge-preview {
  display: flex;
  align-items: center;
  gap: 1rem;
  margin-bottom: 1.25rem;
}

.preview-card {
  flex: 1;
  padding: 0.75rem;
  border-radius: 8px;
  border: 1px solid var(--p-surface-200);
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
}

.preview-card.preferred {
  border-color: var(--p-green-200);
  background: var(--p-green-50);
}

.preview-card.deprecated {
  border-color: var(--p-orange-200);
  background: var(--p-orange-50);
}

.preview-label {
  font-size: 0.6875rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--p-text-muted-color);
}

.preview-id {
  font-size: 0.875rem;
  font-weight: 600;
}

.preview-meta {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.8125rem;
}

.merge-arrow {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.25rem;
  color: var(--p-text-muted-color);
}

.merge-arrow i {
  font-size: 1.25rem;
}

.merge-arrow-label {
  font-size: 0.6875rem;
}

.merge-summary h4 {
  margin: 0 0 0.5rem;
  font-size: 0.875rem;
  font-weight: 600;
}

.merge-summary ul {
  margin: 0;
  padding-left: 1.25rem;
  font-size: 0.8125rem;
  line-height: 1.75;
}

.confirm-box {
  text-align: center;
  padding: 1.5rem;
}

.confirm-icon {
  font-size: 2.5rem;
  color: var(--p-orange-500);
  margin-bottom: 1rem;
}

.confirm-note {
  font-size: 0.8125rem;
  color: var(--p-text-muted-color);
  margin-top: 0.5rem;
}
</style>
