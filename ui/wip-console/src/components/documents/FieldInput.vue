<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import InputText from 'primevue/inputtext'
import Textarea from 'primevue/textarea'
import InputNumber from 'primevue/inputnumber'
import Checkbox from 'primevue/checkbox'
import DatePicker from 'primevue/datepicker'
import Select from 'primevue/select'
import Button from 'primevue/button'
import Message from 'primevue/message'
import { useDocumentStore } from '@/stores'
import type { FieldDefinition, Term } from '@/types'

const props = defineProps<{
  field: FieldDefinition
  modelValue: unknown
  disabled?: boolean
  errors?: string[]
}>()

const emit = defineEmits<{
  'update:modelValue': [value: unknown]
}>()

const documentStore = useDocumentStore()

// For term fields
const terms = ref<Term[]>([])
const loadingTerms = ref(false)

// For array fields
const arrayValue = computed(() => {
  return (props.modelValue as unknown[]) || []
})

// Load terms for term-type fields
async function loadTerms() {
  if (props.field.type !== 'term' || !props.field.terminology_ref) {
    return
  }

  loadingTerms.value = true
  try {
    terms.value = await documentStore.fetchTermsForTerminology(props.field.terminology_ref)
  } catch (e) {
    console.warn('Failed to load terms:', e)
  } finally {
    loadingTerms.value = false
  }
}

// Term options for select dropdown
const termOptions = computed(() => {
  return terms.value.map(t => ({
    label: t.label,
    value: t.code
  }))
})

// Helper to parse date strings
function parseDate(value: unknown): Date | null {
  if (!value) return null
  if (value instanceof Date) return value
  if (typeof value === 'string') {
    const date = new Date(value)
    return isNaN(date.getTime()) ? null : date
  }
  return null
}

// Format date for API (ISO string, date only)
function formatDateForApi(date: Date | null): string | null {
  if (!date) return null
  return date.toISOString().split('T')[0]
}

// Format datetime for API (ISO string)
function formatDateTimeForApi(date: Date | null): string | null {
  if (!date) return null
  return date.toISOString()
}

// Handlers for different field types
function handleStringInput(value: string | undefined) {
  emit('update:modelValue', value || null)
}

function handleNumberInput(value: number | null) {
  emit('update:modelValue', value)
}

function handleBooleanInput(value: boolean) {
  emit('update:modelValue', value)
}

function handleDateInput(value: Date | Date[] | (Date | null)[] | null | undefined) {
  // DatePicker can return various types, extract single Date
  let date: Date | null = null
  if (value instanceof Date) {
    date = value
  } else if (Array.isArray(value) && value[0] instanceof Date) {
    date = value[0]
  }
  const formatted = formatDateForApi(date)
  emit('update:modelValue', formatted)
}

function handleDateTimeInput(value: Date | Date[] | (Date | null)[] | null | undefined) {
  // DatePicker can return various types, extract single Date
  let date: Date | null = null
  if (value instanceof Date) {
    date = value
  } else if (Array.isArray(value) && value[0] instanceof Date) {
    date = value[0]
  }
  const formatted = formatDateTimeForApi(date)
  emit('update:modelValue', formatted)
}

function handleTermInput(value: string | null) {
  emit('update:modelValue', value)
}

// Array field handlers
function addArrayItem() {
  const newArray = [...arrayValue.value]
  const defaultItem = getDefaultValueForType(props.field.array_item_type || 'string')
  newArray.push(defaultItem)
  emit('update:modelValue', newArray)
}

function removeArrayItem(index: number) {
  const newArray = [...arrayValue.value]
  newArray.splice(index, 1)
  emit('update:modelValue', newArray)
}

function handleArrayItemUpdate(index: number, value: unknown) {
  const newArray = [...arrayValue.value]
  newArray[index] = value
  emit('update:modelValue', newArray)
}

function getDefaultValueForType(type: string): unknown {
  switch (type) {
    case 'string': return ''
    case 'number': return null
    case 'integer': return null
    case 'boolean': return false
    case 'date': return null
    case 'datetime': return null
    case 'term': return null
    case 'object': return {}
    case 'array': return []
    default: return null
  }
}

// Create a pseudo-field for array items
const arrayItemField = computed((): FieldDefinition => ({
  name: `${props.field.name}_item`,
  label: 'Item',
  type: props.field.array_item_type || 'string',
  mandatory: false,
  terminology_ref: props.field.array_terminology_ref,
  template_ref: props.field.array_template_ref,
  metadata: {}
}))

// Validation hints
const validationHints = computed(() => {
  const hints: string[] = []
  const v = props.field.validation

  if (v) {
    if (v.pattern) hints.push(`Pattern: ${v.pattern}`)
    if (v.min_length !== undefined) hints.push(`Min length: ${v.min_length}`)
    if (v.max_length !== undefined) hints.push(`Max length: ${v.max_length}`)
    if (v.minimum !== undefined) hints.push(`Min: ${v.minimum}`)
    if (v.maximum !== undefined) hints.push(`Max: ${v.maximum}`)
    if (v.enum && v.enum.length > 0) hints.push(`Allowed: ${v.enum.join(', ')}`)
  }

  return hints
})

// Watch for terminology changes and reload terms
watch(() => props.field.terminology_ref, () => {
  if (props.field.type === 'term') {
    loadTerms()
  }
}, { immediate: true })

onMounted(() => {
  if (props.field.type === 'term') {
    loadTerms()
  }
})
</script>

<template>
  <div class="field-input">
    <!-- String field -->
    <template v-if="field.type === 'string'">
      <InputText
        v-if="!field.validation?.max_length || field.validation.max_length <= 200"
        :modelValue="(modelValue as string) || ''"
        @update:modelValue="handleStringInput"
        :disabled="disabled"
        :placeholder="field.label"
        class="w-full"
      />
      <Textarea
        v-else
        :modelValue="(modelValue as string) || ''"
        @update:modelValue="handleStringInput"
        :disabled="disabled"
        :placeholder="field.label"
        rows="3"
        class="w-full"
      />
    </template>

    <!-- Number field -->
    <template v-else-if="field.type === 'number'">
      <InputNumber
        :modelValue="modelValue as number | null"
        @update:modelValue="handleNumberInput"
        :disabled="disabled"
        :min="field.validation?.minimum"
        :max="field.validation?.maximum"
        mode="decimal"
        :minFractionDigits="0"
        :maxFractionDigits="10"
        class="w-full"
      />
    </template>

    <!-- Integer field -->
    <template v-else-if="field.type === 'integer'">
      <InputNumber
        :modelValue="modelValue as number | null"
        @update:modelValue="handleNumberInput"
        :disabled="disabled"
        :min="field.validation?.minimum"
        :max="field.validation?.maximum"
        :useGrouping="false"
        class="w-full"
      />
    </template>

    <!-- Boolean field -->
    <template v-else-if="field.type === 'boolean'">
      <div class="checkbox-wrapper">
        <Checkbox
          :modelValue="modelValue as boolean"
          @update:modelValue="handleBooleanInput"
          :disabled="disabled"
          :binary="true"
          :inputId="field.name"
        />
        <label :for="field.name" class="checkbox-label">{{ field.label }}</label>
      </div>
    </template>

    <!-- Date field -->
    <template v-else-if="field.type === 'date'">
      <DatePicker
        :modelValue="parseDate(modelValue)"
        @update:modelValue="handleDateInput"
        :disabled="disabled"
        dateFormat="yy-mm-dd"
        showIcon
        class="w-full"
      />
    </template>

    <!-- DateTime field -->
    <template v-else-if="field.type === 'datetime'">
      <DatePicker
        :modelValue="parseDate(modelValue)"
        @update:modelValue="handleDateTimeInput"
        :disabled="disabled"
        dateFormat="yy-mm-dd"
        showTime
        hourFormat="24"
        showIcon
        class="w-full"
      />
    </template>

    <!-- Term field -->
    <template v-else-if="field.type === 'term'">
      <Select
        :modelValue="modelValue as string | null"
        @update:modelValue="handleTermInput"
        :options="termOptions"
        optionLabel="label"
        optionValue="value"
        :disabled="disabled"
        :loading="loadingTerms"
        :placeholder="loadingTerms ? 'Loading...' : `Select ${field.label}`"
        filter
        showClear
        class="w-full"
      />
      <small v-if="field.terminology_ref" class="terminology-ref">
        From: {{ field.terminology_ref }}
      </small>
    </template>

    <!-- Object field (nested) -->
    <template v-else-if="field.type === 'object'">
      <div class="object-field">
        <div v-if="field.template_ref" class="object-notice">
          <Message severity="info" :closable="false">
            Nested object from template: {{ field.template_ref }}
          </Message>
          <p class="template-ref-note">
            Object fields with template references require the nested template's fields to be loaded.
            This feature is not fully implemented yet.
          </p>
        </div>
        <div v-else class="object-placeholder">
          <Message severity="warn" :closable="false">
            Object field without template reference. Add fields manually via JSON.
          </Message>
        </div>
      </div>
    </template>

    <!-- Array field -->
    <template v-else-if="field.type === 'array'">
      <div class="array-field">
        <div v-for="(item, index) in arrayValue" :key="index" class="array-item">
          <div class="array-item-content">
            <FieldInput
              :field="arrayItemField"
              :modelValue="item"
              @update:modelValue="(v) => handleArrayItemUpdate(index, v)"
              :disabled="disabled"
            />
          </div>
          <Button
            icon="pi pi-times"
            severity="danger"
            text
            rounded
            size="small"
            @click="removeArrayItem(index)"
            :disabled="disabled"
            v-tooltip="'Remove'"
          />
        </div>
        <Button
          label="Add Item"
          icon="pi pi-plus"
          severity="secondary"
          text
          size="small"
          @click="addArrayItem"
          :disabled="disabled"
        />
      </div>
    </template>

    <!-- Unknown type fallback -->
    <template v-else>
      <InputText
        :modelValue="String(modelValue || '')"
        @update:modelValue="handleStringInput"
        :disabled="disabled"
        :placeholder="field.label"
        class="w-full"
      />
      <small class="unknown-type">Unknown field type: {{ field.type }}</small>
    </template>

    <!-- Validation hints -->
    <div v-if="validationHints.length > 0" class="validation-hints">
      <small v-for="hint in validationHints" :key="hint">{{ hint }}</small>
    </div>

    <!-- Errors -->
    <div v-if="errors && errors.length > 0" class="field-errors">
      <small v-for="err in errors" :key="err" class="error-text">{{ err }}</small>
    </div>
  </div>
</template>

<style scoped>
.field-input {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.w-full {
  width: 100%;
}

.checkbox-wrapper {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.checkbox-label {
  cursor: pointer;
}

.terminology-ref {
  color: var(--p-text-muted-color);
  font-size: 0.75rem;
}

.object-field {
  border: 1px solid var(--p-surface-200);
  border-radius: var(--p-border-radius);
  padding: 1rem;
  background-color: var(--p-surface-50);
}

.object-notice,
.object-placeholder {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.template-ref-note {
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
  margin: 0;
}

.array-field {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.array-item {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
}

.array-item-content {
  flex: 1;
}

.validation-hints {
  display: flex;
  flex-direction: column;
  gap: 0.125rem;
}

.validation-hints small {
  color: var(--p-text-muted-color);
  font-size: 0.75rem;
}

.field-errors {
  display: flex;
  flex-direction: column;
  gap: 0.125rem;
}

.error-text {
  color: var(--p-red-500);
  font-size: 0.75rem;
}

.unknown-type {
  color: var(--p-orange-500);
  font-size: 0.75rem;
}
</style>
