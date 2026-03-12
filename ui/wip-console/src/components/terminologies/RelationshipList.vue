<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import DataTable, { type DataTablePageEvent } from 'primevue/datatable'
import Column from 'primevue/column'
import Button from 'primevue/button'
import Select from 'primevue/select'
import Tag from 'primevue/tag'
import { useConfirm } from 'primevue/useconfirm'
import { useUiStore } from '@/stores'
import { defStoreClient } from '@/api/client'
import type { Relationship } from '@/types'
import TruncatedId from '@/components/common/TruncatedId.vue'
import RelationshipForm from './RelationshipForm.vue'

const props = defineProps<{
  terminologyId: string
  termId?: string
}>()

const emit = defineEmits<{
  navigateToTerm: [termId: string]
}>()

const confirm = useConfirm()
const uiStore = useUiStore()

const relationships = ref<Relationship[]>([])
const total = ref(0)
const loading = ref(false)
const currentPage = ref(0)
const rowsPerPage = ref(50)
const showCreateDialog = ref(false)

const directionFilter = ref<string>('both')
const typeFilter = ref<string | null>(null)

const directionOptions = [
  { label: 'All directions', value: 'both' },
  { label: 'Outgoing', value: 'outgoing' },
  { label: 'Incoming', value: 'incoming' },
]

const typeOptions = computed(() => {
  const types = new Set<string>()
  for (const rel of relationships.value) {
    types.add(rel.relationship_type)
  }
  const opts = [{ label: 'All types', value: null as string | null }]
  for (const t of types) {
    opts.push({ label: t, value: t })
  }
  return opts
})

watch(
  [() => props.terminologyId, () => props.termId],
  () => {
    currentPage.value = 0
    loadRelationships()
  },
  { immediate: true }
)

watch([directionFilter, typeFilter], () => {
  currentPage.value = 0
  loadRelationships()
})

async function loadRelationships() {
  loading.value = true
  try {
    if (props.termId) {
      // Per-term relationships
      const data = await defStoreClient.listRelationships({
        term_id: props.termId,
        direction: directionFilter.value,
        relationship_type: typeFilter.value || undefined,
        page: currentPage.value + 1,
        page_size: rowsPerPage.value,
      })
      relationships.value = data.items
      total.value = data.total
    } else {
      // All relationships for this terminology (use /all endpoint with filter)
      const data = await defStoreClient.listAllRelationships({
        source_terminology_id: props.terminologyId,
        page: currentPage.value + 1,
        page_size: rowsPerPage.value,
        relationship_type: typeFilter.value || undefined,
      })
      relationships.value = data.items
      total.value = data.total
    }
  } catch (e) {
    uiStore.showError('Failed to load relationships', (e as Error).message)
  } finally {
    loading.value = false
  }
}

function onPage(event: DataTablePageEvent) {
  currentPage.value = event.page
  rowsPerPage.value = event.rows
  loadRelationships()
}

function getTypeSeverity(type: string): 'success' | 'warn' | 'info' | 'secondary' | undefined {
  switch (type) {
    case 'is_a': return 'info'
    case 'part_of': return 'success'
    case 'maps_to': return 'warn'
    default: return 'secondary'
  }
}

function confirmDelete(rel: Relationship) {
  confirm.require({
    message: `Delete relationship: ${rel.source_term_id} --${rel.relationship_type}--> ${rel.target_term_id}?`,
    header: 'Delete Relationship',
    icon: 'pi pi-exclamation-triangle',
    rejectClass: 'p-button-secondary p-button-text',
    acceptClass: 'p-button-danger',
    accept: async () => {
      try {
        await defStoreClient.deleteRelationships([{
          source_term_id: rel.source_term_id,
          target_term_id: rel.target_term_id,
          relationship_type: rel.relationship_type,
        }])
        uiStore.showSuccess('Relationship deleted')
        await loadRelationships()
      } catch (e) {
        uiStore.showError('Delete failed', (e as Error).message)
      }
    }
  })
}

async function onRelationshipCreated() {
  showCreateDialog.value = false
  await loadRelationships()
}
</script>

<template>
  <div class="relationship-list">
    <div class="list-header">
      <div class="filters">
        <Select
          v-if="props.termId"
          v-model="directionFilter"
          :options="directionOptions"
          option-label="label"
          option-value="value"
          placeholder="Direction"
          class="filter-select"
        />
        <Select
          v-model="typeFilter"
          :options="typeOptions"
          option-label="label"
          option-value="value"
          placeholder="Type"
          class="filter-select"
        />
      </div>
      <Button
        label="Add Relationship"
        icon="pi pi-plus"
        size="small"
        @click="showCreateDialog = true"
      />
    </div>

    <DataTable
      :value="relationships"
      :loading="loading"
      :lazy="true"
      :paginator="true"
      :rows="rowsPerPage"
      :totalRecords="total"
      :rowsPerPageOptions="[25, 50, 100]"
      :first="currentPage * rowsPerPage"
      @page="onPage"
      stripedRows
      size="small"
      dataKey="source_term_id"
      class="rel-table"
    >
      <template #empty>
        <div class="empty-state">
          <i class="pi pi-sitemap" style="font-size: 2rem; opacity: 0.3"></i>
          <p>No relationships found</p>
        </div>
      </template>

      <Column header="Source" style="min-width: 180px">
        <template #body="{ data }">
          <div class="term-cell">
            <a href="#" class="term-link" @click.prevent="emit('navigateToTerm', data.source_term_id)">
              <TruncatedId :id="data.source_term_id" :length="12" :show-copy="false" />
            </a>
            <span v-if="data.source_term_value" class="term-value">{{ data.source_term_value }}</span>
          </div>
        </template>
      </Column>

      <Column header="Type" style="width: 140px">
        <template #body="{ data }">
          <Tag
            :value="data.relationship_type"
            :severity="getTypeSeverity(data.relationship_type)"
          />
        </template>
      </Column>

      <Column header="Target" style="min-width: 180px">
        <template #body="{ data }">
          <div class="term-cell">
            <a href="#" class="term-link" @click.prevent="emit('navigateToTerm', data.target_term_id)">
              <TruncatedId :id="data.target_term_id" :length="12" :show-copy="false" />
            </a>
            <span v-if="data.target_term_value" class="term-value">{{ data.target_term_value }}</span>
          </div>
        </template>
      </Column>

      <Column field="status" header="Status" style="width: 100px">
        <template #body="{ data }">
          <Tag
            :value="data.status"
            :severity="data.status === 'active' ? 'info' : 'danger'"
          />
        </template>
      </Column>

      <Column header="" style="width: 60px">
        <template #body="{ data }">
          <Button
            icon="pi pi-trash"
            severity="danger"
            text
            rounded
            size="small"
            title="Delete"
            @click="confirmDelete(data)"
          />
        </template>
      </Column>
    </DataTable>

    <RelationshipForm
      v-model:visible="showCreateDialog"
      :default-source-term-id="props.termId"
      @created="onRelationshipCreated"
    />
  </div>
</template>

<style scoped>
.relationship-list {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.list-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.75rem;
}

.filters {
  display: flex;
  gap: 0.5rem;
}

.filter-select {
  min-width: 150px;
}

.term-cell {
  display: flex;
  flex-direction: column;
  gap: 0.125rem;
}

.term-link {
  text-decoration: none;
  color: var(--p-primary-color);
}

.term-link:hover {
  text-decoration: underline;
}

.term-value {
  font-size: 0.8rem;
  color: var(--p-text-muted-color);
  font-family: monospace;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.5rem;
  padding: 2rem;
  color: var(--p-text-muted-color);
}
</style>
