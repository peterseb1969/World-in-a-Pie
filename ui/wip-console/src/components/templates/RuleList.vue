<script setup lang="ts">
import { ref, computed } from 'vue'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Button from 'primevue/button'
import Tag from 'primevue/tag'
import RuleForm from './RuleForm.vue'
import type { ValidationRule, FieldDefinition } from '@/types'
import { RULE_TYPES } from '@/types'

const props = defineProps<{
  rules: ValidationRule[]
  fields: FieldDefinition[]
  editable: boolean
}>()

const emit = defineEmits<{
  update: [rules: ValidationRule[]]
}>()

const showDialog = ref(false)
const editingIndex = ref<number | null>(null)
const editingRule = ref<ValidationRule | null>(null)

const localRules = computed({
  get: () => props.rules,
  set: (value) => emit('update', value)
})

function openAddDialog() {
  editingIndex.value = null
  editingRule.value = null
  showDialog.value = true
}

function openEditDialog(index: number) {
  editingIndex.value = index
  editingRule.value = JSON.parse(JSON.stringify(props.rules[index]))
  showDialog.value = true
}

function saveRule(rule: ValidationRule) {
  const newRules = [...props.rules]

  if (editingIndex.value !== null) {
    newRules[editingIndex.value] = rule
  } else {
    newRules.push(rule)
  }

  emit('update', newRules)
  showDialog.value = false
}

function deleteRule(index: number) {
  const newRules = props.rules.filter((_, i) => i !== index)
  emit('update', newRules)
}

function getRuleTypeLabel(type: string): string {
  return RULE_TYPES.find(r => r.value === type)?.label || type
}

function getRuleTypeSeverity(type: string): "success" | "info" | "warn" | "danger" | "secondary" | "contrast" | undefined {
  switch (type) {
    case 'conditional_required':
      return 'danger'
    case 'conditional_value':
      return 'warn'
    case 'mutual_exclusion':
      return 'info'
    case 'dependency':
      return 'secondary'
    case 'pattern':
      return 'success'
    case 'range':
      return 'success'
    default:
      return 'info'
  }
}

function formatConditions(rule: ValidationRule): string {
  if (!rule.conditions || rule.conditions.length === 0) {
    return '-'
  }

  return rule.conditions.map(c => {
    const valueStr = c.value !== undefined ? ` ${JSON.stringify(c.value)}` : ''
    return `${c.field} ${c.operator}${valueStr}`
  }).join(', ')
}

function formatTarget(rule: ValidationRule): string {
  if (rule.target_fields && rule.target_fields.length > 0) {
    return rule.target_fields.join(', ')
  }
  if (rule.target_field) {
    return rule.target_field
  }
  return '-'
}
</script>

<template>
  <div class="rule-list">
    <div class="list-header" v-if="editable">
      <Button
        label="Add Rule"
        icon="pi pi-plus"
        size="small"
        @click="openAddDialog"
      />
    </div>

    <DataTable
      :value="localRules"
      stripedRows
      class="rules-table"
    >
      <Column field="type" header="Type" style="width: 180px">
        <template #body="{ data }">
          <Tag :value="getRuleTypeLabel(data.type)" :severity="getRuleTypeSeverity(data.type)" />
        </template>
      </Column>

      <Column header="Target" style="min-width: 150px">
        <template #body="{ data }">
          <code class="target-field">{{ formatTarget(data) }}</code>
        </template>
      </Column>

      <Column header="Conditions" style="min-width: 200px">
        <template #body="{ data }">
          <span class="conditions">{{ formatConditions(data) }}</span>
        </template>
      </Column>

      <Column field="description" header="Description" style="min-width: 200px">
        <template #body="{ data }">
          <span class="description">{{ data.description || '-' }}</span>
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
              @click="deleteRule(index)"
            />
          </div>
        </template>
      </Column>

      <template #empty>
        <div class="empty-state">
          <i class="pi pi-check-square"></i>
          <p>No validation rules defined</p>
          <Button
            v-if="editable"
            label="Add your first rule"
            icon="pi pi-plus"
            size="small"
            @click="openAddDialog"
          />
        </div>
      </template>
    </DataTable>

    <RuleForm
      v-model:visible="showDialog"
      :rule="editingRule"
      :fields="fields"
      @save="saveRule"
    />
  </div>
</template>

<style scoped>
.rule-list {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.list-header {
  display: flex;
  justify-content: flex-end;
}

.target-field {
  background-color: var(--p-surface-100);
  padding: 0.125rem 0.375rem;
  border-radius: var(--p-border-radius);
  font-size: 0.875rem;
}

.conditions {
  font-size: 0.875rem;
  color: var(--p-text-muted-color);
  font-family: monospace;
}

.description {
  font-size: 0.875rem;
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
</style>
