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
import FileField from './FileField.vue'
import Dialog from 'primevue/dialog'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import { useDocumentStore, useTemplateStore, useNamespaceStore } from '@/stores'
import { templateStoreClient, documentStoreClient } from '@/api/client'
import { getDocumentTitle } from '@/utils/document'
import type { FieldDefinition, Term, Template } from '@/types'
import { SEMANTIC_TYPES } from '@/types'

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
const namespaceStore = useNamespaceStore()

// For term fields
const terms = ref<Term[]>([])
const loadingTerms = ref(false)

// For object fields with template_ref
const nestedTemplate = ref<Template | null>(null)
const loadingNestedTemplate = ref(false)

// For array fields
const arrayValue = computed(() => {
  return (props.modelValue as unknown[]) || []
})

// Load nested template for object-type fields
async function loadNestedTemplate() {
  if (props.field.type !== 'object' || !props.field.template_ref) {
    return
  }

  loadingNestedTemplate.value = true
  try {
    nestedTemplate.value = await templateStoreClient.getTemplate(props.field.template_ref)
  } catch (e) {
    console.warn('Failed to load nested template:', e)
    nestedTemplate.value = null
  } finally {
    loadingNestedTemplate.value = false
  }
}

// Handle nested object field updates
function handleObjectFieldUpdate(fieldName: string, value: unknown) {
  const current = (props.modelValue as Record<string, unknown>) || {}
  const updated = { ...current }

  if (value === null || value === undefined || value === '') {
    delete updated[fieldName]
  } else {
    updated[fieldName] = value
  }

  emit('update:modelValue', updated)
}

// Get value of a nested object field
function getObjectFieldValue(fieldName: string): unknown {
  const current = (props.modelValue as Record<string, unknown>) || {}
  return current[fieldName] ?? null
}

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
    value: t.value
  })).sort((a, b) => (a.label || '').localeCompare(b.label || ''))
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

function handleReferenceInput(value: string | undefined) {
  emit('update:modelValue', value || null)
}

// Document reference search
const showRefSearch = ref(false)
const refSearchResults = ref<Array<Record<string, unknown>>>([])
const refSearchLoading = ref(false)
const refSearchQuery = ref('')
const refTemplateFilter = ref<string | null>(null)
const templateStore = useTemplateStore()

// Template options for reference search filter
const refTemplateOptions = computed(() => {
  // If target_templates is specified, only show those
  if (props.field.target_templates?.length) {
    return templateStore.templates
      .filter(t => props.field.target_templates!.includes(t.template_id) || props.field.target_templates!.includes(t.value))
      .map(t => ({ label: `${t.label} (${t.value})`, value: t.template_id }))
      .sort((a, b) => a.label.localeCompare(b.label))
  }
  return templateStore.templates
    .filter(t => t.status === 'active')
    .map(t => ({ label: `${t.label} (${t.value})`, value: t.template_id }))
    .sort((a, b) => a.label.localeCompare(b.label))
})

// Whether template filter should be locked (single target template)
const refTemplateLocked = ref(false)

// Resolve a template code to a template_id
function resolveTemplateId(codeOrId: string): string {
  const tpl = templateStore.templates.find(t => t.value === codeOrId || t.template_id === codeOrId)
  return tpl ? tpl.template_id : codeOrId
}

async function openRefSearch() {
  refSearchQuery.value = ''
  refSearchResults.value = []
  showRefSearch.value = true
  // Ensure templates are loaded for filter dropdown
  if (templateStore.templates.length === 0) {
    await templateStore.fetchTemplates({ page_size: 100 })
  }
  // Pre-set and lock template filter when field restricts to one template
  if (props.field.target_templates?.length === 1) {
    refTemplateFilter.value = resolveTemplateId(props.field.target_templates[0])
    refTemplateLocked.value = true
  } else {
    refTemplateFilter.value = null
    refTemplateLocked.value = false
  }
  await searchDocuments()
}

async function searchDocuments() {
  refSearchLoading.value = true
  try {
    const params: Record<string, unknown> = {
      page_size: 50,
      namespace: namespaceStore.currentNamespaceParam
    }
    if (refTemplateFilter.value) {
      params.template_id = refTemplateFilter.value
    }
    const result = await documentStoreClient.listDocuments(params as Parameters<typeof documentStoreClient.listDocuments>[0])
    let items = result.items as unknown as Array<Record<string, unknown>>
    // Client-side search filter (API doesn't support text search)
    if (refSearchQuery.value.trim()) {
      const q = refSearchQuery.value.toLowerCase()
      items = items.filter(doc => {
        const id = (doc.document_id as string || '').toLowerCase()
        const title = getDocumentTitle(doc as any).toLowerCase()
        const data = JSON.stringify(doc.data || {}).toLowerCase()
        return id.includes(q) || title.includes(q) || data.includes(q)
      })
    }
    refSearchResults.value = items
  } catch {
    refSearchResults.value = []
  } finally {
    refSearchLoading.value = false
  }
}

// Get template name for a document
function getDocTemplateName(doc: Record<string, unknown>): string {
  const tplId = doc.template_id as string
  if (!tplId) return '-'
  const tpl = templateStore.templates.find(t => t.template_id === tplId)
  return tpl ? tpl.label : tplId.slice(0, 12)
}

// Get a readable preview of document data (first few key-value pairs)
function getDocDataPreview(doc: Record<string, unknown>): string {
  const data = doc.data as Record<string, unknown> | undefined
  if (!data) return '-'
  const entries = Object.entries(data)
    .filter(([, v]) => v !== null && v !== undefined && v !== '')
    .slice(0, 3)
    .map(([k, v]) => {
      const val = typeof v === 'object' ? JSON.stringify(v) : String(v)
      return `${k}: ${val.length > 30 ? val.slice(0, 30) + '...' : val}`
    })
  return entries.join(' | ') || '-'
}

function selectRefDocument(doc: Record<string, unknown>) {
  emit('update:modelValue', doc.document_id)
  showRefSearch.value = false
}

// Reference field helper text
const referenceHelpText = computed(() => {
  if (props.field.type !== 'reference') return ''

  const refType = props.field.reference_type
  switch (refType) {
    case 'document':
      return 'Enter document ID, hash:identity_hash, or business key'
    case 'term':
      return 'Enter term value (e.g., COUNTRY:United States) or UUID'
    case 'terminology':
      return 'Enter terminology value (e.g., COUNTRY) or UUID'
    case 'template':
      return 'Enter template value (e.g., PATIENT_RECORD) or UUID'
    default:
      return 'Enter reference value'
  }
})

const referenceTargetInfo = computed(() => {
  if (props.field.type !== 'reference') return ''

  const refType = props.field.reference_type
  if (refType === 'document' && props.field.target_templates?.length) {
    return `Allowed templates: ${props.field.target_templates.join(', ')}`
  }
  if (refType === 'term' && props.field.target_terminologies?.length) {
    return `Allowed terminologies: ${props.field.target_terminologies.join(', ')}`
  }
  return ''
})

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
    case 'reference': return null
    case 'file': return null
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
  // Pass through reference-related properties for array items
  reference_type: props.field.reference_type,
  target_templates: props.field.target_templates,
  target_terminologies: props.field.target_terminologies,
  version_strategy: props.field.version_strategy,
  // Pass through file config for array items
  file_config: props.field.array_file_config,
  metadata: {}
}))

// Semantic type info
const semanticTypeInfo = computed(() => {
  if (!props.field.semantic_type) return null
  return SEMANTIC_TYPES.find(st => st.value === props.field.semantic_type) || null
})

// Get semantic type constraint hints
const semanticConstraintHints = computed(() => {
  const hints: string[] = []
  const st = props.field.semantic_type

  if (st === 'email') {
    hints.push('Valid email address required')
  } else if (st === 'url') {
    hints.push('Valid HTTP(S) URL required')
  } else if (st === 'latitude') {
    hints.push('Range: -90 to 90')
  } else if (st === 'longitude') {
    hints.push('Range: -180 to 180')
  } else if (st === 'percentage') {
    hints.push('Range: 0 to 100')
  } else if (st === 'duration') {
    hints.push('Format: {value, unit} where unit is seconds, minutes, hours, days, or weeks')
  } else if (st === 'geo_point') {
    hints.push('Format: {latitude, longitude}')
  }

  return hints
})

// Validation hints
const validationHints = computed(() => {
  const hints: string[] = []
  const v = props.field.validation

  // Add semantic type constraint hints first
  hints.push(...semanticConstraintHints.value)

  if (v) {
    if (v.pattern) hints.push(`Pattern: ${v.pattern}`)
    // Skip min_length <= 1 (redundant with mandatory indicator)
    if (v.min_length && v.min_length > 1) hints.push(`Min length: ${v.min_length}`)
    if (v.max_length && v.max_length > 0) hints.push(`Max length: ${v.max_length}`)
    if (v.minimum !== undefined && v.minimum !== null) hints.push(`Min: ${v.minimum}`)
    if (v.maximum !== undefined && v.maximum !== null) hints.push(`Max: ${v.maximum}`)
    if (v.enum && v.enum.length > 0) hints.push(`Allowed: ${v.enum.join(', ')}`)
  }

  return hints
})

// Combined tooltip for all constraints (HTML with escape:false)
const constraintTooltip = computed(() => {
  const parts: string[] = []
  if (semanticTypeInfo.value) {
    parts.push(`<strong>${semanticTypeInfo.value.label}</strong>`)
  }
  parts.push(...validationHints.value)
  return { value: parts.join('<br>'), escape: false }
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
  if (props.field.type === 'object' && props.field.template_ref) {
    loadNestedTemplate()
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

    <!-- Reference field -->
    <template v-else-if="field.type === 'reference'">
      <div class="reference-field">
        <div class="reference-input-row">
          <InputText
            :modelValue="(modelValue as string) || ''"
            @update:modelValue="handleReferenceInput"
            :disabled="disabled"
            :placeholder="referenceHelpText"
            class="w-full"
          />
          <Button
            v-if="field.reference_type === 'document' && !disabled"
            icon="pi pi-search"
            severity="secondary"
            @click="openRefSearch"
            v-tooltip="'Search documents'"
          />
        </div>
        <div class="reference-info">
          <small class="reference-type">
            <i class="pi pi-link"></i>
            {{ field.reference_type || 'document' }} reference
          </small>
          <small v-if="referenceTargetInfo" class="reference-targets">
            {{ referenceTargetInfo }}
          </small>
          <small v-if="field.version_strategy" class="reference-strategy">
            Strategy: {{ field.version_strategy }}
          </small>
        </div>

        <!-- Reference Search Dialog -->
        <Dialog
          v-model:visible="showRefSearch"
          header="Select Document"
          :style="{ width: '800px' }"
          modal
        >
          <div class="ref-search">
            <div class="ref-search-filters">
              <Select
                v-model="refTemplateFilter"
                :options="refTemplateOptions"
                optionLabel="label"
                optionValue="value"
                placeholder="All templates"
                :showClear="!refTemplateLocked"
                :disabled="refTemplateLocked"
                filter
                class="ref-template-filter"
                @change="searchDocuments"
              />
              <div class="ref-search-bar">
                <InputText
                  v-model="refSearchQuery"
                  placeholder="Search by ID or data..."
                  class="w-full"
                  @keyup.enter="searchDocuments"
                />
                <Button icon="pi pi-search" @click="searchDocuments" :loading="refSearchLoading" />
              </div>
            </div>
            <small class="ref-search-hint">Click a row to select the document</small>
            <DataTable
              :value="refSearchResults"
              :loading="refSearchLoading"
              size="small"
              @row-click="(e: any) => selectRefDocument(e.data)"
              :pt="{ bodyRow: { style: 'cursor: pointer' } }"
              scrollable
              scrollHeight="350px"
            >
              <Column header="Title / ID" style="width: 160px">
                <template #body="{ data }">
                  <span v-if="getDocumentTitle(data) !== data.document_id" class="ref-doc-title">{{ getDocumentTitle(data) }}</span>
                  <code v-else class="ref-doc-id">{{ (data.document_id as string)?.slice(0, 10) }}</code>
                </template>
              </Column>
              <Column header="Template" style="width: 130px">
                <template #body="{ data }">
                  <span class="ref-template-name">{{ getDocTemplateName(data) }}</span>
                </template>
              </Column>
              <Column header="Data">
                <template #body="{ data }">
                  <span class="ref-data-preview">{{ getDocDataPreview(data) }}</span>
                </template>
              </Column>
              <Column field="version" header="Ver" style="width: 50px" />
              <template #empty>
                <div style="text-align: center; padding: 1rem; color: var(--p-text-muted-color)">
                  {{ refSearchLoading ? 'Searching...' : 'No documents found. Try a different search or template filter.' }}
                </div>
              </template>
            </DataTable>
          </div>
        </Dialog>
      </div>
    </template>

    <!-- File field -->
    <template v-else-if="field.type === 'file'">
      <FileField
        :field="field"
        :modelValue="modelValue as string | string[] | null"
        @update:modelValue="(v) => emit('update:modelValue', v)"
        :disabled="disabled"
        :errors="errors"
      />
    </template>

    <!-- Object field (nested) -->
    <template v-else-if="field.type === 'object'">
      <div class="object-field">
        <!-- Loading nested template -->
        <div v-if="loadingNestedTemplate" class="object-loading">
          <i class="pi pi-spin pi-spinner"></i>
          <span>Loading nested template...</span>
        </div>

        <!-- Nested template fields -->
        <div v-else-if="field.template_ref && nestedTemplate" class="object-nested">
          <div class="object-nested-header">
            <small class="nested-template-ref">
              <i class="pi pi-sitemap"></i>
              {{ nestedTemplate.label }} ({{ nestedTemplate.value }})
            </small>
          </div>
          <div class="object-nested-fields">
            <div
              v-for="nestedField in nestedTemplate.fields"
              :key="nestedField.name"
              class="nested-field"
            >
              <label class="nested-field-label">
                {{ nestedField.label }}
                <span v-if="nestedField.mandatory" class="required-indicator">*</span>
              </label>
              <FieldInput
                :field="nestedField"
                :modelValue="getObjectFieldValue(nestedField.name)"
                @update:modelValue="(v) => handleObjectFieldUpdate(nestedField.name, v)"
                :disabled="disabled"
              />
            </div>
          </div>
        </div>

        <!-- Template ref but failed to load -->
        <div v-else-if="field.template_ref && !nestedTemplate" class="object-notice">
          <Message severity="warn" :closable="false">
            Could not load nested template: {{ field.template_ref }}
          </Message>
        </div>

        <!-- Plain object without template ref -->
        <div v-else class="object-placeholder">
          <Textarea
            :modelValue="modelValue ? JSON.stringify(modelValue, null, 2) : '{}'"
            @update:modelValue="(v) => { try { emit('update:modelValue', JSON.parse(v || '{}')); } catch {} }"
            :disabled="disabled"
            placeholder='{"key": "value"}'
            rows="4"
            class="w-full object-json-editor"
          />
          <small class="object-hint">Enter structured data as JSON</small>
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

    <!-- Compact constraint info — tooltip only -->
    <div v-if="validationHints.length > 0 || semanticTypeInfo" class="constraint-info">
      <span
        class="constraint-icon"
        v-tooltip.bottom="constraintTooltip"
      >
        <i :class="semanticTypeInfo?.icon || 'pi pi-info-circle'"></i>
        <small v-if="semanticTypeInfo">{{ semanticTypeInfo.label }}</small>
      </span>
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

.reference-field {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.reference-input-row {
  display: flex;
  gap: 0.25rem;
}

.ref-search {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.ref-search-filters {
  display: flex;
  gap: 0.5rem;
}

.ref-template-filter {
  min-width: 200px;
}

.ref-search-bar {
  display: flex;
  gap: 0.5rem;
  flex: 1;
}

.ref-search-hint {
  color: var(--p-text-muted-color);
  font-size: 0.75rem;
}

.ref-template-name {
  font-size: 0.8rem;
  font-weight: 500;
}

.ref-doc-title {
  font-weight: 500;
  font-size: 0.8rem;
}

.ref-doc-id {
  font-size: 0.7rem;
  background: var(--p-surface-100);
  padding: 0.125rem 0.25rem;
  border-radius: 3px;
}

.ref-data-preview {
  font-size: 0.8rem;
  color: var(--p-text-muted-color);
  max-width: 350px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  display: block;
}

.reference-info {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  align-items: center;
}

.reference-type {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  color: var(--p-text-muted-color);
  font-size: 0.75rem;
}

.reference-type i {
  font-size: 0.7rem;
}

.reference-targets {
  color: var(--p-primary-color);
  font-size: 0.75rem;
}

.reference-strategy {
  color: var(--p-text-muted-color);
  font-size: 0.75rem;
  font-style: italic;
}

.object-field {
  border: 1px solid var(--p-surface-200);
  border-radius: var(--p-border-radius);
  padding: 1rem;
  background-color: var(--p-surface-50);
}

.object-loading {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
}

.object-notice,
.object-placeholder {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.object-nested-header {
  margin-bottom: 0.75rem;
  padding-bottom: 0.5rem;
  border-bottom: 1px solid var(--p-surface-200);
}

.nested-template-ref {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  color: var(--p-primary-600);
  font-size: 0.8rem;
}

.object-nested-fields {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.nested-field {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
}

.nested-field-label {
  font-weight: 500;
  font-size: 0.8rem;
  color: var(--p-text-color);
}

.nested-field-label .required-indicator {
  color: var(--p-red-500);
}

.object-json-editor {
  font-family: monospace;
  font-size: 0.85rem;
}

.object-hint {
  color: var(--p-text-muted-color);
  font-size: 0.75rem;
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

.constraint-info {
  display: flex;
  align-items: center;
}

.constraint-icon {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  color: var(--p-text-muted-color);
  font-size: 0.75rem;
  cursor: help;
}

.constraint-icon i {
  font-size: 0.7rem;
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
