<script setup lang="ts">
import { ref, watch } from 'vue'
import DataTable, { type DataTablePageEvent } from 'primevue/datatable'
import Column from 'primevue/column'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Tag from 'primevue/tag'
import { useConfirm } from 'primevue/useconfirm'
import { useTermStore, useUiStore } from '@/stores'
import type { Term } from '@/types'
import TermForm from './TermForm.vue'
import DeprecateTermDialog from './DeprecateTermDialog.vue'
import TruncatedId from '@/components/common/TruncatedId.vue'

const props = defineProps<{
  terminologyId: string
}>()

const confirm = useConfirm()
const termStore = useTermStore()
const uiStore = useUiStore()

const showCreateDialog = ref(false)
const showEditDialog = ref(false)
const showDeprecateDialog = ref(false)
const editingTerm = ref<Term | null>(null)
const deprecatingTerm = ref<Term | null>(null)
const searchQuery = ref('')
const currentPage = ref(0) // PrimeVue uses 0-based indexing
const rowsPerPage = ref(50)

// Debounced search
let searchTimeout: ReturnType<typeof setTimeout> | null = null

watch(
  () => props.terminologyId,
  async (newId) => {
    if (newId) {
      currentPage.value = 0
      await loadTerms()
    }
  },
  { immediate: true }
)

watch(searchQuery, () => {
  // Debounce search to avoid too many API calls
  if (searchTimeout) clearTimeout(searchTimeout)
  searchTimeout = setTimeout(() => {
    currentPage.value = 0
    loadTerms()
  }, 300)
})

async function loadTerms() {
  try {
    await termStore.fetchTerms(props.terminologyId, {
      page: currentPage.value + 1, // API uses 1-based indexing
      page_size: rowsPerPage.value,
      search: searchQuery.value || undefined
    })
  } catch (e) {
    uiStore.showError('Failed to load terms', (e as Error).message)
  }
}

function onPage(event: DataTablePageEvent) {
  currentPage.value = event.page
  rowsPerPage.value = event.rows
  loadTerms()
}

function getStatusSeverity(status: string): 'success' | 'warn' | 'danger' | 'info' | 'secondary' | 'contrast' | undefined {
  switch (status) {
    case 'active': return 'info'
    case 'deprecated': return 'warn'
    case 'inactive': return 'danger'
    default: return 'secondary'
  }
}

function editTerm(term: Term) {
  editingTerm.value = term
  showEditDialog.value = true
}

function deprecateTerm(term: Term) {
  deprecatingTerm.value = term
  showDeprecateDialog.value = true
}

function confirmDeactivate(term: Term) {
  confirm.require({
    message: `Are you sure you want to deactivate the term "${term.label}"? It can be restored later.`,
    header: 'Deactivate Term',
    icon: 'pi pi-exclamation-triangle',
    rejectClass: 'p-button-secondary p-button-text',
    acceptClass: 'p-button-danger',
    accept: async () => {
      try {
        await termStore.deleteTerm(term.term_id)
        uiStore.showSuccess('Term Deactivated', `"${term.label}" has been deactivated`)
      } catch (e) {
        uiStore.showError('Deactivation Failed', (e as Error).message)
      }
    }
  })
}

async function onCreated() {
  showCreateDialog.value = false
}

async function onUpdated() {
  showEditDialog.value = false
  editingTerm.value = null
}

async function onDeprecated() {
  showDeprecateDialog.value = false
  deprecatingTerm.value = null
}
</script>

<template>
  <div class="term-list">
    <div class="list-header">
      <h3>Terms</h3>
      <div class="header-actions">
        <span class="p-input-icon-left">
          <i class="pi pi-search" />
          <InputText
            v-model="searchQuery"
            placeholder="Search terms..."
          />
        </span>
        <Button
          label="Add Term"
          icon="pi pi-plus"
          @click="showCreateDialog = true"
        />
      </div>
    </div>

    <DataTable
      :value="termStore.terms"
      :loading="termStore.loading"
      :lazy="true"
      :paginator="true"
      :rows="rowsPerPage"
      :totalRecords="termStore.total"
      :rowsPerPageOptions="[25, 50, 100]"
      :first="currentPage * rowsPerPage"
      @page="onPage"
      stripedRows
      size="small"
      dataKey="term_id"
      class="term-table"
    >
      <template #empty>
        <div class="empty-state">
          <i class="pi pi-inbox" style="font-size: 2rem; opacity: 0.3"></i>
          <p>No terms found</p>
        </div>
      </template>

      <Column field="sort_order" header="#" sortable style="width: 5%">
        <template #body="{ data }">
          <span class="sort-order">{{ data.sort_order }}</span>
        </template>
      </Column>

      <Column field="term_id" header="ID" sortable style="width: 120px">
        <template #body="{ data }">
          <TruncatedId :id="data.term_id" :length="10" />
        </template>
      </Column>

      <Column field="code" header="Code" sortable style="width: 12%">
        <template #body="{ data }">
          <span class="code-badge">{{ data.code }}</span>
        </template>
      </Column>

      <Column field="value" header="Value" sortable style="width: 12%">
        <template #body="{ data }">
          <code class="value-text">{{ data.value }}</code>
        </template>
      </Column>

      <Column field="aliases" header="Aliases" style="width: 15%">
        <template #body="{ data }">
          <div class="aliases-container" v-if="data.aliases?.length">
            <span
              v-for="alias in data.aliases.slice(0, 3)"
              :key="alias"
              class="alias-badge"
            >{{ alias }}</span>
            <span v-if="data.aliases.length > 3" class="alias-more">
              +{{ data.aliases.length - 3 }}
            </span>
          </div>
          <span v-else class="no-aliases">-</span>
        </template>
      </Column>

      <Column field="label" header="Label" sortable style="width: 15%">
        <template #body="{ data }">
          <span>{{ data.label }}</span>
        </template>
      </Column>

      <Column field="description" header="Description" style="width: 18%">
        <template #body="{ data }">
          <span class="description-text">{{ data.description || '-' }}</span>
        </template>
      </Column>

      <Column field="status" header="Status" sortable style="width: 14%">
        <template #body="{ data }">
          <div class="status-cell">
            <Tag :value="data.status" :severity="getStatusSeverity(data.status)" />
            <span v-if="data.status === 'deprecated' && data.replaced_by_term_id" class="replaced-by">
              <i class="pi pi-arrow-right"></i>
              <TruncatedId :id="data.replaced_by_term_id" :length="10" :show-copy="false" />
            </span>
            <span v-if="data.status === 'deprecated' && data.deprecated_reason" class="deprecated-reason" v-tooltip.top="data.deprecated_reason">
              <i class="pi pi-info-circle"></i>
            </span>
          </div>
        </template>
      </Column>

      <Column header="Actions" style="width: 10%">
        <template #body="{ data }">
          <div class="action-buttons">
            <Button
              icon="pi pi-pencil"
              severity="secondary"
              text
              rounded
              size="small"
              title="Edit"
              @click="editTerm(data)"
            />
            <Button
              v-if="data.status === 'active'"
              icon="pi pi-ban"
              severity="warn"
              text
              rounded
              size="small"
              title="Deprecate"
              @click="deprecateTerm(data)"
            />
            <Button
              icon="pi pi-ban"
              severity="danger"
              text
              rounded
              size="small"
              title="Deactivate"
              @click="confirmDeactivate(data)"
            />
          </div>
        </template>
      </Column>
    </DataTable>

    <TermForm
      v-model:visible="showCreateDialog"
      :terminology-id="terminologyId"
      @created="onCreated"
    />

    <TermForm
      v-if="editingTerm"
      v-model:visible="showEditDialog"
      :terminology-id="terminologyId"
      :term="editingTerm"
      @updated="onUpdated"
    />

    <DeprecateTermDialog
      v-if="deprecatingTerm"
      v-model:visible="showDeprecateDialog"
      :term="deprecatingTerm"
      :terms="termStore.terms"
      @deprecated="onDeprecated"
    />
  </div>
</template>

<style scoped>
.term-list {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.list-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 1rem;
}

.list-header h3 {
  margin: 0;
}

.header-actions {
  display: flex;
  gap: 1rem;
  align-items: center;
}

.sort-order {
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
}

.id-badge {
  font-family: monospace;
  color: var(--p-text-muted-color);
  font-size: 0.8rem;
}

.code-badge {
  font-family: monospace;
  background: var(--p-surface-100);
  padding: 0.25rem 0.5rem;
  border-radius: 4px;
  font-size: 0.8rem;
}

.value-text {
  font-family: monospace;
  font-size: 0.85rem;
  background: var(--p-surface-50);
  padding: 0.125rem 0.375rem;
  border-radius: 3px;
}

.aliases-container {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
  align-items: center;
}

.alias-badge {
  font-family: monospace;
  font-size: 0.75rem;
  background: var(--p-primary-50);
  color: var(--p-primary-700);
  padding: 0.125rem 0.375rem;
  border-radius: 3px;
}

.alias-more {
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
}

.no-aliases {
  color: var(--p-text-muted-color);
}

.description-text {
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.status-cell {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  flex-wrap: wrap;
}

.replaced-by {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
}

.replaced-by i {
  font-size: 0.625rem;
}

.deprecated-reason {
  color: var(--p-text-muted-color);
  font-size: 0.75rem;
  cursor: help;
}

.action-buttons {
  display: flex;
  gap: 0.125rem;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.5rem;
  padding: 2rem;
  color: var(--p-text-muted-color);
}

.p-input-icon-left {
  position: relative;
}

.p-input-icon-left > i {
  position: absolute;
  left: 0.75rem;
  top: 50%;
  transform: translateY(-50%);
  color: var(--p-text-muted-color);
}

.p-input-icon-left > input {
  padding-left: 2.5rem;
}
</style>
