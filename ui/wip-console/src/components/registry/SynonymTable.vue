<script setup lang="ts">
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Button from 'primevue/button'
import Tag from 'primevue/tag'
import { useConfirm } from 'primevue/useconfirm'
import CompositeKeyDisplay from './CompositeKeyDisplay.vue'
import type { RegistrySynonym } from '@/types'

defineProps<{
  synonyms: RegistrySynonym[]
  entryId: string
}>()

const emit = defineEmits<{
  remove: [synonym: RegistrySynonym]
  add: []
}>()

const confirm = useConfirm()

function confirmRemove(synonym: RegistrySynonym) {
  confirm.require({
    message: `Remove synonym from ${synonym.namespace}/${synonym.entity_type}?`,
    header: 'Remove Synonym',
    icon: 'pi pi-exclamation-triangle',
    acceptClass: 'p-button-danger',
    accept: () => emit('remove', synonym),
  })
}

function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleString()
}
</script>

<template>
  <div class="synonym-table">
    <div class="table-header">
      <span class="count">{{ synonyms.length }} synonym{{ synonyms.length !== 1 ? 's' : '' }}</span>
      <Button
        icon="pi pi-plus"
        label="Add Synonym"
        size="small"
        outlined
        @click="emit('add')"
      />
    </div>

    <DataTable
      v-if="synonyms.length > 0"
      :value="synonyms"
      size="small"
      stripedRows
      class="syn-data-table"
    >
      <Column header="Namespace" style="width: 120px">
        <template #body="{ data }">
          <code class="namespace-badge">{{ data.namespace }}</code>
        </template>
      </Column>

      <Column header="Entity Type" style="width: 120px">
        <template #body="{ data }">
          <Tag :value="data.entity_type" severity="info" />
        </template>
      </Column>

      <Column header="Composite Key">
        <template #body="{ data }">
          <CompositeKeyDisplay :compositeKey="data.composite_key" compact />
        </template>
      </Column>

      <Column header="Created" style="width: 150px">
        <template #body="{ data }">
          <span class="date-text">{{ formatDate(data.created_at) }}</span>
        </template>
      </Column>

      <Column header="" style="width: 50px">
        <template #body="{ data }">
          <Button
            icon="pi pi-trash"
            text
            rounded
            severity="danger"
            size="small"
            v-tooltip.top="'Remove synonym'"
            @click="confirmRemove(data)"
          />
        </template>
      </Column>
    </DataTable>

    <div v-else class="empty-state">
      No synonyms registered for this entry.
    </div>
  </div>
</template>

<style scoped>
.synonym-table {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.table-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.count {
  font-size: 0.8125rem;
  color: var(--p-text-muted-color);
}

.namespace-badge {
  font-size: 0.75rem;
  background: var(--p-surface-100);
  padding: 0.125rem 0.375rem;
  border-radius: 3px;
}

.date-text {
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
}

.empty-state {
  padding: 1rem;
  text-align: center;
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
}
</style>
