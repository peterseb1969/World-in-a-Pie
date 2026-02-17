<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import InputText from 'primevue/inputtext'
import Tag from 'primevue/tag'
import { useRouter } from 'vue-router'
import { registryClient } from '@/api/client'
import type { RegistrySearchResult } from '@/types'

const router = useRouter()

const query = ref('')
const results = ref<RegistrySearchResult[]>([])
const loading = ref(false)
const showDropdown = ref(false)
const inputRef = ref<{ $el: HTMLElement } | null>(null)
let searchTimeout: ReturnType<typeof setTimeout> | null = null

function onInput() {
  if (searchTimeout) clearTimeout(searchTimeout)
  if (query.value.trim().length < 2) {
    results.value = []
    showDropdown.value = false
    return
  }
  searchTimeout = setTimeout(doSearch, 300)
}

async function doSearch() {
  const q = query.value.trim()
  if (q.length < 2) return

  loading.value = true
  showDropdown.value = true
  try {
    const response = await registryClient.unifiedSearch({ q, page_size: 8 })
    results.value = response.items
  } catch {
    results.value = []
  } finally {
    loading.value = false
  }
}

function selectResult(result: RegistrySearchResult) {
  showDropdown.value = false
  query.value = ''
  results.value = []
  router.push({ name: 'registry-detail', params: { id: result.entry_id } })
}

function getEntityTypeIcon(type: string): string {
  switch (type) {
    case 'terminologies': return 'pi pi-book'
    case 'terms': return 'pi pi-tag'
    case 'templates': return 'pi pi-file'
    case 'documents': return 'pi pi-folder'
    case 'files': return 'pi pi-images'
    default: return 'pi pi-circle'
  }
}

function getStatusSeverity(status: string): 'success' | 'warn' | 'danger' | 'secondary' {
  switch (status) {
    case 'active': return 'success'
    case 'reserved': return 'warn'
    case 'inactive': return 'danger'
    default: return 'secondary'
  }
}

function onBlur() {
  // Delay to allow click on result
  setTimeout(() => {
    showDropdown.value = false
  }, 200)
}

function onFocus() {
  if (results.value.length > 0) {
    showDropdown.value = true
  }
}

// Keyboard shortcut: Cmd+K / Ctrl+K
function onKeyDown(e: KeyboardEvent) {
  if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
    e.preventDefault()
    const el = inputRef.value?.$el
    if (el) {
      el.focus()
    }
  }
}

onMounted(() => {
  document.addEventListener('keydown', onKeyDown)
})

onUnmounted(() => {
  document.removeEventListener('keydown', onKeyDown)
})
</script>

<template>
  <div class="registry-search">
    <div class="search-wrapper">
      <i class="pi pi-search search-icon" />
      <InputText
        ref="inputRef"
        v-model="query"
        placeholder="Search registry (IDs, keys, synonyms...)  Cmd+K"
        @input="onInput"
        @focus="onFocus"
        @blur="onBlur"
        class="search-input"
      />
      <i v-if="loading" class="pi pi-spin pi-spinner loading-icon" />
    </div>

    <div v-if="showDropdown && (results.length > 0 || (query.length >= 2 && !loading))" class="search-dropdown">
      <div
        v-for="result in results"
        :key="result.entry_id"
        class="search-result"
        @mousedown.prevent="selectResult(result)"
      >
        <div class="result-main">
          <i :class="getEntityTypeIcon(result.entity_type)" class="result-icon"></i>
          <code class="result-id">{{ result.entry_id }}</code>
          <code class="result-namespace">{{ result.namespace }}</code>
          <Tag :value="result.status" :severity="getStatusSeverity(result.status)" class="result-status" />
        </div>
        <div class="result-path">
          {{ result.resolution_path }}
        </div>
      </div>

      <div v-if="results.length === 0 && query.length >= 2 && !loading" class="no-results">
        No results found for "{{ query }}"
      </div>
    </div>
  </div>
</template>

<style scoped>
.registry-search {
  position: relative;
  width: 100%;
}

.search-wrapper {
  position: relative;
  display: flex;
  align-items: center;
}

.search-icon {
  position: absolute;
  left: 0.875rem;
  color: var(--p-text-muted-color);
  font-size: 1rem;
  z-index: 1;
}

.loading-icon {
  position: absolute;
  right: 0.875rem;
  color: var(--p-text-muted-color);
}

.search-input {
  width: 100%;
  padding-left: 2.5rem;
  padding-right: 2.5rem;
  font-size: 0.9375rem;
  height: 2.75rem;
}

.search-dropdown {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  right: 0;
  background: var(--p-surface-0);
  border: 1px solid var(--p-surface-200);
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.1);
  z-index: 100;
  max-height: 400px;
  overflow-y: auto;
}

.search-result {
  padding: 0.625rem 0.875rem;
  cursor: pointer;
  border-bottom: 1px solid var(--p-surface-100);
  transition: background-color 0.1s;
}

.search-result:hover {
  background: var(--p-surface-50);
}

.search-result:last-child {
  border-bottom: none;
}

.result-main {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.result-icon {
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
}

.result-id {
  font-size: 0.8125rem;
  font-weight: 600;
}

.result-namespace {
  font-size: 0.6875rem;
  background: var(--p-surface-100);
  padding: 0.0625rem 0.375rem;
  border-radius: 3px;
}

.result-path {
  margin-top: 0.25rem;
  padding-left: 1.25rem;
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
}

.no-results {
  padding: 1rem;
  text-align: center;
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
}
</style>
