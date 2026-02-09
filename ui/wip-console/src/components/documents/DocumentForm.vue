<script setup lang="ts">
import { computed } from 'vue'
import FieldInput from './FieldInput.vue'
import type { Template, DocumentValidationError } from '@/types'

const props = defineProps<{
  template: Template
  modelValue: Record<string, unknown>
  disabled?: boolean
  validationErrors?: DocumentValidationError[]
}>()

const emit = defineEmits<{
  'update:modelValue': [value: Record<string, unknown>]
}>()

// Field types that should span full width (2 columns)
const fullWidthTypes = new Set(['object', 'array', 'file', 'reference'])

// Check if a field should span full width
function isFullWidth(field: typeof props.template.fields[0]): boolean {
  if (fullWidthTypes.has(field.type)) return true
  // Textarea fields (string with long max_length)
  if (field.type === 'string' && field.validation?.max_length && field.validation.max_length > 200) return true
  return false
}

// Get sorted fields (mandatory first, then alphabetically)
const sortedFields = computed(() => {
  return [...props.template.fields].sort((a, b) => {
    // Mandatory fields first
    if (a.mandatory && !b.mandatory) return -1
    if (!a.mandatory && b.mandatory) return 1
    // Then by label alphabetically
    return a.label.localeCompare(b.label)
  })
})

// Get errors for a specific field
function getFieldErrors(fieldName: string): string[] {
  if (!props.validationErrors) return []
  return props.validationErrors
    .filter(e => e.field === fieldName)
    .map(e => e.message)
}

// Check if field has errors
function hasFieldError(fieldName: string): boolean {
  return getFieldErrors(fieldName).length > 0
}

// Update a field value
function updateField(fieldName: string, value: unknown) {
  const newData = { ...props.modelValue }

  // Handle null/empty values
  if (value === null || value === undefined || value === '') {
    delete newData[fieldName]
  } else {
    newData[fieldName] = value
  }

  emit('update:modelValue', newData)
}

// Get the current value for a field
function getFieldValue(fieldName: string): unknown {
  return props.modelValue[fieldName] ?? null
}

// Check if field is an identity field
function isIdentityField(fieldName: string): boolean {
  return props.template.identity_fields.includes(fieldName)
}
</script>

<template>
  <div class="document-form">
    <div
      v-for="field in sortedFields"
      :key="field.name"
      class="form-field"
      :class="{ 'has-error': hasFieldError(field.name), 'full-width': isFullWidth(field) }"
    >
      <label :for="field.name" class="field-label">
        {{ field.label }}
        <span v-if="field.mandatory" class="required-indicator">*</span>
        <span v-if="isIdentityField(field.name)" class="identity-indicator" v-tooltip="'Identity field - used to uniquely identify this document'">
          <i class="pi pi-key"></i>
        </span>
      </label>

      <FieldInput
        :field="field"
        :modelValue="getFieldValue(field.name)"
        @update:modelValue="(v) => updateField(field.name, v)"
        :disabled="disabled"
        :errors="getFieldErrors(field.name)"
      />

      <small v-if="field.default_value !== undefined" class="default-value">
        Default: {{ field.default_value }}
      </small>
    </div>

    <div v-if="sortedFields.length === 0" class="no-fields">
      <p>This template has no fields defined.</p>
    </div>
  </div>
</template>

<style scoped>
.document-form {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1rem;
}

.form-field {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.form-field.full-width {
  grid-column: span 2;
}

.form-field.has-error {
  border-left: 3px solid var(--p-red-500);
  padding-left: 0.75rem;
}

@media (max-width: 768px) {
  .document-form {
    grid-template-columns: 1fr;
  }

  .form-field.full-width {
    grid-column: span 1;
  }
}

.field-label {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-weight: 500;
  font-size: 0.875rem;
  color: var(--p-text-color);
}

.required-indicator {
  color: var(--p-red-500);
}

.identity-indicator {
  color: var(--p-primary-color);
  font-size: 0.75rem;
}

.identity-indicator i {
  font-size: 0.75rem;
}

.default-value {
  color: var(--p-text-muted-color);
  font-size: 0.75rem;
}

.no-fields {
  text-align: center;
  padding: 2rem;
  color: var(--p-text-muted-color);
}

.no-fields p {
  margin: 0;
}
</style>
