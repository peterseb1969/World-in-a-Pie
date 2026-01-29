<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Tag from 'primevue/tag'
import { useConfirm } from 'primevue/useconfirm'
import { useTerminologyStore, useUiStore } from '@/stores'
import type { Terminology } from '@/types'
import TerminologyForm from './TerminologyForm.vue'

const router = useRouter()
const confirm = useConfirm()
const terminologyStore = useTerminologyStore()
const uiStore = useUiStore()

const showCreateDialog = ref(false)
const showEditDialog = ref(false)
const editingTerminology = ref<Terminology | null>(null)
const searchQuery = ref('')

const filteredTerminologies = computed(() => {
  if (!searchQuery.value) return terminologyStore.terminologies
  const query = searchQuery.value.toLowerCase()
  return terminologyStore.terminologies.filter(t =>
    t.code.toLowerCase().includes(query) ||
    t.name.toLowerCase().includes(query) ||
    t.description?.toLowerCase().includes(query)
  )
})

onMounted(async () => {
  try {
    await terminologyStore.fetchTerminologies()
  } catch (e) {
    uiStore.showError('Failed to load terminologies', (e as Error).message)
  }
})

function getStatusSeverity(status: string): 'success' | 'warn' | 'danger' | 'info' | 'secondary' | 'contrast' | undefined {
  switch (status) {
    case 'active': return 'success'
    case 'deprecated': return 'warn'
    case 'inactive': return 'danger'
    default: return 'secondary'
  }
}

function viewTerminology(terminology: Terminology) {
  router.push(`/terminologies/${terminology.terminology_id}`)
}

function editTerminology(terminology: Terminology) {
  editingTerminology.value = terminology
  showEditDialog.value = true
}

function confirmDelete(terminology: Terminology) {
  confirm.require({
    message: `Are you sure you want to delete "${terminology.name}"? This will also delete all terms in this terminology.`,
    header: 'Delete Terminology',
    icon: 'pi pi-exclamation-triangle',
    rejectClass: 'p-button-secondary p-button-text',
    acceptClass: 'p-button-danger',
    accept: async () => {
      try {
        await terminologyStore.deleteTerminology(terminology.terminology_id)
        uiStore.showSuccess('Terminology Deleted', `"${terminology.name}" has been deleted`)
      } catch (e) {
        uiStore.showError('Delete Failed', (e as Error).message)
      }
    }
  })
}

async function onCreated() {
  showCreateDialog.value = false
  await terminologyStore.fetchTerminologies()
}

async function onUpdated() {
  showEditDialog.value = false
  editingTerminology.value = null
}
</script>

<template>
  <div class="terminology-list">
    <div class="list-header">
      <h2>Terminologies</h2>
      <div class="header-actions">
        <span class="p-input-icon-left">
          <i class="pi pi-search" />
          <InputText
            v-model="searchQuery"
            placeholder="Search terminologies..."
          />
        </span>
        <Button
          label="New Terminology"
          icon="pi pi-plus"
          @click="showCreateDialog = true"
        />
      </div>
    </div>

    <DataTable
      :value="filteredTerminologies"
      :loading="terminologyStore.loading"
      striped-rows
      paginator
      :rows="10"
      :rows-per-page-options="[10, 25, 50]"
      data-key="terminology_id"
      class="terminology-table"
    >
      <template #empty>
        <div class="empty-state">
          <i class="pi pi-inbox" style="font-size: 3rem; opacity: 0.3"></i>
          <p>No terminologies found</p>
        </div>
      </template>

      <Column field="code" header="Code" sortable style="width: 15%">
        <template #body="{ data }">
          <span class="code-badge">{{ data.code }}</span>
        </template>
      </Column>

      <Column field="name" header="Name" sortable style="width: 25%">
        <template #body="{ data }">
          <a
            href="#"
            class="terminology-link"
            @click.prevent="viewTerminology(data)"
          >
            {{ data.name }}
          </a>
        </template>
      </Column>

      <Column field="description" header="Description" style="width: 30%">
        <template #body="{ data }">
          <span class="description-text">{{ data.description || '-' }}</span>
        </template>
      </Column>

      <Column field="term_count" header="Terms" sortable style="width: 8%">
        <template #body="{ data }">
          <span class="term-count">{{ data.term_count }}</span>
        </template>
      </Column>

      <Column field="status" header="Status" sortable style="width: 10%">
        <template #body="{ data }">
          <Tag :value="data.status" :severity="getStatusSeverity(data.status)" />
        </template>
      </Column>

      <Column header="Actions" style="width: 12%">
        <template #body="{ data }">
          <div class="action-buttons">
            <Button
              icon="pi pi-eye"
              severity="secondary"
              text
              rounded
              title="View"
              @click="viewTerminology(data)"
            />
            <Button
              icon="pi pi-pencil"
              severity="secondary"
              text
              rounded
              title="Edit"
              @click="editTerminology(data)"
            />
            <Button
              icon="pi pi-trash"
              severity="danger"
              text
              rounded
              title="Delete"
              @click="confirmDelete(data)"
            />
          </div>
        </template>
      </Column>
    </DataTable>

    <TerminologyForm
      v-model:visible="showCreateDialog"
      @created="onCreated"
    />

    <TerminologyForm
      v-if="editingTerminology"
      v-model:visible="showEditDialog"
      :terminology="editingTerminology"
      @updated="onUpdated"
    />
  </div>
</template>

<style scoped>
.terminology-list {
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

.list-header h2 {
  margin: 0;
}

.header-actions {
  display: flex;
  gap: 1rem;
  align-items: center;
}

.code-badge {
  font-family: monospace;
  background: var(--p-surface-100);
  padding: 0.25rem 0.5rem;
  border-radius: 4px;
  font-size: 0.875rem;
}

.terminology-link {
  color: var(--p-primary-color);
  text-decoration: none;
  font-weight: 500;
}

.terminology-link:hover {
  text-decoration: underline;
}

.description-text {
  color: var(--p-text-muted-color);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.term-count {
  font-weight: 600;
}

.action-buttons {
  display: flex;
  gap: 0.25rem;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1rem;
  padding: 3rem;
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
