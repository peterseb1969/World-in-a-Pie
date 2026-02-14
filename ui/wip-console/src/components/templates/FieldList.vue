<script setup lang="ts">
import { ref, computed } from 'vue'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Button from 'primevue/button'
import Tag from 'primevue/tag'
import FieldForm from './FieldForm.vue'
import type { FieldDefinition, Terminology, Template } from '@/types'

const props = defineProps<{
  fields: FieldDefinition[]
  editable: boolean
  terminologies: Terminology[]
  templates: Template[]
  inheritedFields?: FieldDefinition[]
  parentName?: string
}>()

const emit = defineEmits<{
  update: [fields: FieldDefinition[]]
}>()

const showDialog = ref(false)
const editingIndex = ref<number | null>(null)
const editingField = ref<FieldDefinition | null>(null)

const localFields = computed({
  get: () => props.fields,
  set: (value) => emit('update', value)
})

function openAddDialog() {
  editingIndex.value = null
  editingField.value = null
  showDialog.value = true
}

function openEditDialog(index: number) {
  editingIndex.value = index
  editingField.value = JSON.parse(JSON.stringify(props.fields[index]))
  showDialog.value = true
}

function saveField(field: FieldDefinition) {
  const newFields = [...props.fields]

  if (editingIndex.value !== null) {
    newFields[editingIndex.value] = field
  } else {
    newFields.push(field)
  }

  emit('update', newFields)
  showDialog.value = false
}

function deleteField(index: number) {
  const newFields = props.fields.filter((_, i) => i !== index)
  emit('update', newFields)
}

function moveField(index: number, direction: 'up' | 'down') {
  const newFields = [...props.fields]
  const newIndex = direction === 'up' ? index - 1 : index + 1

  if (newIndex < 0 || newIndex >= newFields.length) return

  const temp = newFields[index]
  newFields[index] = newFields[newIndex]
  newFields[newIndex] = temp

  emit('update', newFields)
}

function getTypeSeverity(type: string): "success" | "info" | "warn" | "danger" | "secondary" | "contrast" | undefined {
  switch (type) {
    case 'string':
      return 'info'
    case 'number':
    case 'integer':
      return 'success'
    case 'boolean':
      return 'warn'
    case 'date':
    case 'datetime':
      return 'secondary'
    case 'term':
    case 'reference':
      return 'contrast'
    case 'object':
    case 'array':
      return 'danger'
    default:
      return 'info'
  }
}

function getTerminologyName(id: string | undefined): string {
  if (!id) return ''
  const term = props.terminologies.find(t => t.terminology_id === id || t.value === id)
  return term ? term.label : id
}

function getTemplateName(id: string | undefined): string {
  if (!id) return ''
  const tpl = props.templates.find(t => t.template_id === id || t.value === id)
  return tpl ? tpl.label : id
}
</script>

<template>
  <div class="field-list">
    <div class="list-header" v-if="editable">
      <div class="field-types-help">
        <i class="pi pi-info-circle"></i>
        <span>
          <strong>Tip:</strong> Use <em>Term</em> type for controlled vocabulary (values from terminologies).
          <em>Object</em> type for nested templates. Basic types (string, number, etc.) allow free-form input.
        </span>
      </div>
      <Button
        label="Add Field"
        icon="pi pi-plus"
        size="small"
        @click="openAddDialog"
      />
    </div>

    <!-- Inherited fields (read-only) -->
    <div v-if="inheritedFields && inheritedFields.length > 0" class="inherited-section">
      <div class="inherited-header">
        <i class="pi pi-share-alt"></i>
        <span>Inherited from <strong>{{ parentName || 'parent' }}</strong> (read-only)</span>
      </div>
      <DataTable
        :value="inheritedFields"
        size="small"
        class="fields-table inherited-table"
      >
        <Column field="name" header="Name" style="min-width: 150px">
          <template #body="{ data }">
            <div class="field-name">
              <code>{{ data.name }}</code>
              <Tag v-if="data.mandatory" value="Required" severity="danger" />
            </div>
          </template>
        </Column>
        <Column field="label" header="Label" style="min-width: 150px" />
        <Column field="type" header="Type" style="width: 120px">
          <template #body="{ data }">
            <Tag :value="data.type" :severity="getTypeSeverity(data.type)" />
          </template>
        </Column>
        <Column header="Reference" style="min-width: 150px">
          <template #body="{ data }">
            <div class="reference-info" v-if="data.type === 'term' && data.terminology_ref">
              <i class="pi pi-book"></i>
              {{ getTerminologyName(data.terminology_ref) }}
            </div>
            <div class="reference-info" v-else-if="data.type === 'object' && data.template_ref">
              <i class="pi pi-file-edit"></i>
              {{ getTemplateName(data.template_ref) }}
            </div>
            <span v-else class="no-reference">-</span>
          </template>
        </Column>
      </DataTable>
    </div>

    <!-- Own fields -->
    <DataTable
      :value="localFields"
      stripedRows
      size="small"
      class="fields-table"
    >
      <Column header="#" style="width: 60px" v-if="editable">
        <template #body="{ index }">
          <div class="order-buttons">
            <Button
              icon="pi pi-chevron-up"
              severity="secondary"
              text
              rounded
              size="small"
              :disabled="index === 0"
              @click="moveField(index, 'up')"
            />
            <Button
              icon="pi pi-chevron-down"
              severity="secondary"
              text
              rounded
              size="small"
              :disabled="index === localFields.length - 1"
              @click="moveField(index, 'down')"
            />
          </div>
        </template>
      </Column>

      <Column field="name" header="Name" style="min-width: 150px">
        <template #body="{ data }">
          <div class="field-name">
            <code>{{ data.name }}</code>
            <Tag v-if="data.mandatory" value="Required" severity="danger" />
          </div>
        </template>
      </Column>

      <Column field="label" header="Label" style="min-width: 150px" />

      <Column field="type" header="Type" style="width: 120px">
        <template #body="{ data }">
          <Tag :value="data.type" :severity="getTypeSeverity(data.type)" />
        </template>
      </Column>

      <Column header="Reference" style="min-width: 150px">
        <template #body="{ data }">
          <div class="reference-info" v-if="data.type === 'term' && data.terminology_ref">
            <i class="pi pi-book"></i>
            {{ getTerminologyName(data.terminology_ref) }}
          </div>
          <div class="reference-info" v-else-if="data.type === 'object' && data.template_ref">
            <i class="pi pi-file-edit"></i>
            {{ getTemplateName(data.template_ref) }}
          </div>
          <div class="reference-info" v-else-if="data.type === 'reference'">
            <i class="pi pi-link"></i>
            <span v-if="data.reference_type === 'document'">
              {{ data.target_templates?.join(', ') || 'document' }}
            </span>
            <span v-else-if="data.reference_type === 'term'">
              {{ data.target_terminologies?.join(', ') || 'term' }}
            </span>
            <span v-else>
              {{ data.reference_type }}
            </span>
          </div>
          <div class="reference-info" v-else-if="data.type === 'array'">
            <span v-if="data.array_item_type === 'term' && data.array_terminology_ref">
              <i class="pi pi-book"></i>
              {{ getTerminologyName(data.array_terminology_ref) }}[]
            </span>
            <span v-else-if="data.array_item_type === 'object' && data.array_template_ref">
              <i class="pi pi-file-edit"></i>
              {{ getTemplateName(data.array_template_ref) }}[]
            </span>
            <span v-else-if="data.array_item_type">
              {{ data.array_item_type }}[]
            </span>
          </div>
          <span v-else class="no-reference">-</span>
        </template>
      </Column>

      <Column header="Validation" style="min-width: 150px">
        <template #body="{ data }">
          <div class="validation-info" v-if="data.validation">
            <span v-if="data.validation.pattern" class="validation-badge">
              pattern
            </span>
            <span v-if="data.validation.min_length || data.validation.max_length" class="validation-badge">
              length
            </span>
            <span v-if="data.validation.minimum !== undefined || data.validation.maximum !== undefined" class="validation-badge">
              range
            </span>
            <span v-if="data.validation.enum?.length" class="validation-badge">
              enum
            </span>
          </div>
          <span v-else class="no-reference">-</span>
        </template>
      </Column>

      <Column header="Actions" style="width: 100px" v-if="editable">
        <template #body="{ index }">
          <div class="actions">
            <Button
              icon="pi pi-pencil"
              severity="secondary"
              text
              rounded
              size="small"
              @click="openEditDialog(index)"
            />
            <Button
              icon="pi pi-trash"
              severity="danger"
              text
              rounded
              size="small"
              @click="deleteField(index)"
            />
          </div>
        </template>
      </Column>

      <template #empty>
        <div class="empty-state">
          <i class="pi pi-inbox"></i>
          <p>No fields defined</p>
          <p class="empty-hint" v-if="editable">
            Add fields to define your document structure. Use <strong>Term</strong> type for controlled vocabulary.
          </p>
          <Button
            v-if="editable"
            label="Add your first field"
            icon="pi pi-plus"
            size="small"
            @click="openAddDialog"
          />
        </div>
      </template>
    </DataTable>

    <FieldForm
      v-model:visible="showDialog"
      :field="editingField"
      :terminologies="terminologies"
      :templates="templates"
      :existingNames="localFields.map(f => f.name).filter((_, i) => i !== editingIndex)"
      @save="saveField"
    />
  </div>
</template>

<style scoped>
.field-list {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.list-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 1rem;
}

.field-types-help {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  background-color: var(--p-blue-50);
  border-radius: var(--p-border-radius);
  font-size: 0.8125rem;
  color: var(--p-blue-700);
  flex: 1;
}

.field-types-help i {
  margin-top: 0.125rem;
  flex-shrink: 0;
}

.field-types-help em {
  font-style: normal;
  font-weight: 600;
  color: var(--p-blue-900);
}

.order-buttons {
  display: flex;
  flex-direction: column;
  gap: 0;
}

.field-name {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.field-name code {
  background-color: var(--p-surface-100);
  padding: 0.125rem 0.375rem;
  border-radius: var(--p-border-radius);
  font-size: 0.875rem;
}

.reference-info {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.875rem;
}

.reference-info i {
  color: var(--p-text-muted-color);
}

.no-reference {
  color: var(--p-text-muted-color);
}

.validation-info {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
}

.validation-badge {
  font-size: 0.75rem;
  padding: 0.125rem 0.375rem;
  background-color: var(--p-surface-100);
  border-radius: var(--p-border-radius);
  color: var(--p-text-muted-color);
}

.actions {
  display: flex;
  gap: 0.25rem;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.75rem;
  padding: 2rem;
  color: var(--p-text-muted-color);
}

.empty-state i {
  font-size: 2rem;
}

.empty-hint {
  font-size: 0.875rem;
  max-width: 300px;
  text-align: center;
}

.empty-hint strong {
  color: var(--p-primary-color);
}

.inherited-section {
  border: 1px solid var(--p-surface-200);
  border-radius: var(--p-border-radius);
  overflow: hidden;
  opacity: 0.7;
}

.inherited-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  background: var(--p-surface-50);
  border-bottom: 1px solid var(--p-surface-200);
  font-size: 0.8125rem;
  color: var(--p-text-muted-color);
}

.inherited-table :deep(.p-datatable-tbody > tr) {
  background: var(--p-surface-50);
}
</style>
