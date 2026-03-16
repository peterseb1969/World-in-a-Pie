<script setup lang="ts">
import { ref, onMounted, computed, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Tag from 'primevue/tag'
import Panel from 'primevue/panel'
import { useConfirm } from 'primevue/useconfirm'
import { useTerminologyStore, useUiStore, useNamespaceStore } from '@/stores'
import type { Terminology } from '@/types'
import TerminologyForm from './TerminologyForm.vue'
import TruncatedId from '@/components/common/TruncatedId.vue'

const router = useRouter()
const route = useRoute()
const confirm = useConfirm()
const terminologyStore = useTerminologyStore()
const namespaceStore = useNamespaceStore()
const uiStore = useUiStore()

const showCreateDialog = ref(false)
const showEditDialog = ref(false)
const editingTerminology = ref<Terminology | null>(null)
const searchQuery = ref('')
const wipCollapsed = ref(false)

// Filter own terminologies
const filteredOwnTerminologies = computed(() => {
  if (!searchQuery.value) return terminologyStore.ownTerminologies
  const query = searchQuery.value.toLowerCase()
  return terminologyStore.ownTerminologies.filter(t =>
    t.terminology_id?.toLowerCase().includes(query) ||
    t.value.toLowerCase().includes(query) ||
    t.label.toLowerCase().includes(query) ||
    t.description?.toLowerCase().includes(query)
  )
})

// Filter WIP terminologies
const filteredWipTerminologies = computed(() => {
  if (!searchQuery.value) return terminologyStore.wipTerminologies
  const query = searchQuery.value.toLowerCase()
  return terminologyStore.wipTerminologies.filter(t =>
    t.terminology_id?.toLowerCase().includes(query) ||
    t.value.toLowerCase().includes(query) ||
    t.label.toLowerCase().includes(query) ||
    t.description?.toLowerCase().includes(query)
  )
})

// Get namespace prefix for display
const currentNamespacePrefix = computed(() => namespaceStore.current.toUpperCase())

async function loadTerminologies() {
  try {
    await terminologyStore.fetchTerminologies()
  } catch (e) {
    uiStore.showError('Failed to load terminologies', (e as Error).message)
  }
}

// Reload when namespace changes
watch(() => terminologyStore.namespaceParam, loadTerminologies)

onMounted(async () => {
  await loadTerminologies()
  // Auto-open create dialog if ?create=true
  if (route.query.create === 'true') {
    showCreateDialog.value = true
    router.replace({ query: {} })
  }
})

// Also watch for query changes (same-route navigation from sidebar)
watch(() => route.query.create, (val) => {
  if (val === 'true') {
    showCreateDialog.value = true
    router.replace({ query: {} })
  }
})

function getStatusSeverity(status: string): 'success' | 'warn' | 'danger' | 'info' | 'secondary' | 'contrast' | undefined {
  switch (status) {
    case 'active': return 'info'
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

function confirmDeactivate(terminology: Terminology) {
  confirm.require({
    message: `Are you sure you want to deactivate "${terminology.label}"? This will also deactivate all terms. It can be restored later.`,
    header: 'Deactivate Terminology',
    icon: 'pi pi-exclamation-triangle',
    rejectClass: 'p-button-secondary p-button-text',
    acceptClass: 'p-button-danger',
    accept: async () => {
      try {
        await terminologyStore.deleteTerminology(terminology.terminology_id)
        uiStore.showSuccess('Terminology Deactivated', `"${terminology.label}" has been deactivated`)
      } catch (e) {
        uiStore.showError('Deactivation Failed', (e as Error).message)
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

    <!-- Own Namespace Terminologies -->
    <div class="section-header">
      <Tag :value="currentNamespacePrefix" severity="info" />
      <span class="section-title">{{ currentNamespacePrefix }} Terminologies</span>
      <span class="section-count">({{ terminologyStore.total }})</span>
    </div>

    <DataTable
      :value="filteredOwnTerminologies"
      :loading="terminologyStore.loading"
      striped-rows
      paginator
      :rows="10"
      :rows-per-page-options="[10, 25, 50]"
      size="small"
      data-key="terminology_id"
      class="terminology-table"
    >
      <template #empty>
        <div class="empty-state">
          <i class="pi pi-inbox" style="font-size: 2rem; opacity: 0.3"></i>
          <p>No terminologies in this namespace</p>
        </div>
      </template>

      <Column field="terminology_id" header="ID" sortable style="width: 120px">
        <template #body="{ data }">
          <TruncatedId :id="data.terminology_id" :length="12" />
        </template>
      </Column>

      <Column field="value" header="Value" sortable style="width: 15%">
        <template #body="{ data }">
          <span class="code-badge">{{ data.value }}</span>
        </template>
      </Column>

      <Column field="label" header="Label" sortable style="width: 20%">
        <template #body="{ data }">
          <a
            href="#"
            class="terminology-link"
            @click.prevent="viewTerminology(data)"
          >
            {{ data.label }}
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
              icon="pi pi-ban"
              severity="danger"
              text
              rounded
              title="Deactivate"
              @click="confirmDeactivate(data)"
            />
          </div>
        </template>
      </Column>
    </DataTable>

    <!-- WIP Terminologies (collapsible, read-only) -->
    <Panel
      v-if="terminologyStore.showWipSection"
      :collapsed="wipCollapsed"
      toggleable
      class="wip-section"
      @update:collapsed="wipCollapsed = $event"
    >
      <template #header>
        <div class="wip-header">
          <Tag value="WIP" severity="secondary" />
          <span class="section-title">WIP Terminologies (Read-only)</span>
          <span class="section-count">({{ terminologyStore.wipTotal }})</span>
        </div>
      </template>

      <DataTable
        :value="filteredWipTerminologies"
        :loading="terminologyStore.loading"
        striped-rows
        paginator
        :rows="10"
        :rows-per-page-options="[10, 25, 50]"
        size="small"
        data-key="terminology_id"
        class="terminology-table wip-table"
      >
        <template #empty>
          <div class="empty-state">
            <i class="pi pi-inbox" style="font-size: 2rem; opacity: 0.3"></i>
            <p>No WIP terminologies available</p>
          </div>
        </template>

        <Column field="value" header="Value" sortable style="width: 15%">
          <template #body="{ data }">
            <span class="code-badge">{{ data.value }}</span>
          </template>
        </Column>

        <Column field="label" header="Label" sortable style="width: 30%">
          <template #body="{ data }">
            <a
              href="#"
              class="terminology-link"
              @click.prevent="viewTerminology(data)"
            >
              {{ data.label }}
            </a>
          </template>
        </Column>

        <Column field="description" header="Description" style="width: 30%">
          <template #body="{ data }">
            <span class="description-text">{{ data.description || '-' }}</span>
          </template>
        </Column>

        <Column field="term_count" header="Terms" sortable style="width: 10%">
          <template #body="{ data }">
            <span class="term-count">{{ data.term_count }}</span>
          </template>
        </Column>

        <Column field="status" header="Status" sortable style="width: 10%">
          <template #body="{ data }">
            <Tag :value="data.status" :severity="getStatusSeverity(data.status)" />
          </template>
        </Column>

        <Column header="Actions" style="width: 5%">
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
            </div>
          </template>
        </Column>
      </DataTable>
    </Panel>

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

.section-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0;
  border-bottom: 1px solid var(--p-surface-200);
}

.section-title {
  font-weight: 600;
  color: var(--p-text-color);
}

.section-count {
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
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

/* WIP Section styling */
.wip-section {
  margin-top: 1rem;
}

.wip-section :deep(.p-panel-header) {
  background: var(--p-surface-50);
  border-color: var(--p-surface-200);
}

.wip-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.wip-table {
  opacity: 0.9;
}

.wip-table :deep(.p-datatable-tbody > tr) {
  background: var(--p-surface-50);
}
</style>
