<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Tag from 'primevue/tag'
import { useConfirm } from 'primevue/useconfirm'
import { useTermStore, useUiStore } from '@/stores'
import type { Term } from '@/types'
import TermForm from './TermForm.vue'
import DeprecateTermDialog from './DeprecateTermDialog.vue'

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

const filteredTerms = computed(() => {
  if (!searchQuery.value) return termStore.terms
  const query = searchQuery.value.toLowerCase()
  return termStore.terms.filter(t =>
    t.code.toLowerCase().includes(query) ||
    t.value.toLowerCase().includes(query) ||
    t.label.toLowerCase().includes(query) ||
    t.description?.toLowerCase().includes(query)
  )
})

watch(
  () => props.terminologyId,
  async (newId) => {
    if (newId) {
      await loadTerms()
    }
  },
  { immediate: true }
)

async function loadTerms() {
  try {
    await termStore.fetchTerms(props.terminologyId)
  } catch (e) {
    uiStore.showError('Failed to load terms', (e as Error).message)
  }
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

function confirmDelete(term: Term) {
  confirm.require({
    message: `Are you sure you want to delete the term "${term.label}"?`,
    header: 'Delete Term',
    icon: 'pi pi-exclamation-triangle',
    rejectClass: 'p-button-secondary p-button-text',
    acceptClass: 'p-button-danger',
    accept: async () => {
      try {
        await termStore.deleteTerm(term.term_id)
        uiStore.showSuccess('Term Deleted', `"${term.label}" has been deleted`)
      } catch (e) {
        uiStore.showError('Delete Failed', (e as Error).message)
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
      :value="filteredTerms"
      :loading="termStore.loading"
      striped-rows
      paginator
      :rows="15"
      :rows-per-page-options="[15, 30, 50]"
      data-key="term_id"
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

      <Column field="term_id" header="ID" sortable style="width: 12%">
        <template #body="{ data }">
          <span class="id-badge">{{ data.term_id }}</span>
        </template>
      </Column>

      <Column field="code" header="Code" sortable style="width: 12%">
        <template #body="{ data }">
          <span class="code-badge">{{ data.code }}</span>
        </template>
      </Column>

      <Column field="value" header="Value" sortable style="width: 15%">
        <template #body="{ data }">
          <code class="value-text">{{ data.value }}</code>
        </template>
      </Column>

      <Column field="label" header="Label" sortable style="width: 20%">
        <template #body="{ data }">
          <span>{{ data.label }}</span>
        </template>
      </Column>

      <Column field="description" header="Description" style="width: 25%">
        <template #body="{ data }">
          <span class="description-text">{{ data.description || '-' }}</span>
        </template>
      </Column>

      <Column field="status" header="Status" sortable style="width: 10%">
        <template #body="{ data }">
          <Tag :value="data.status" :severity="getStatusSeverity(data.status)" />
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
              icon="pi pi-trash"
              severity="danger"
              text
              rounded
              size="small"
              title="Delete"
              @click="confirmDelete(data)"
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

.description-text {
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
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
