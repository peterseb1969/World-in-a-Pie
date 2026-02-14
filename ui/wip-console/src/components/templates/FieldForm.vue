<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import InputNumber from 'primevue/inputnumber'
import Select from 'primevue/select'
import Checkbox from 'primevue/checkbox'
import Button from 'primevue/button'
import Fieldset from 'primevue/fieldset'
import type { FieldDefinition, Terminology, Template } from '@/types'
import { FIELD_TYPES, REFERENCE_TYPES, VERSION_STRATEGIES } from '@/types'
import MultiSelect from 'primevue/multiselect'

const props = defineProps<{
  visible: boolean
  field: FieldDefinition | null
  terminologies: Terminology[]
  templates: Template[]
  existingNames: string[]
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  save: [field: FieldDefinition]
}>()

const form = ref<FieldDefinition>({
  name: '',
  label: '',
  type: 'string',
  mandatory: false,
  default_value: undefined,
  terminology_ref: undefined,
  template_ref: undefined,
  reference_type: undefined,
  target_templates: undefined,
  target_terminologies: undefined,
  version_strategy: undefined,
  array_item_type: undefined,
  array_terminology_ref: undefined,
  array_template_ref: undefined,
  validation: undefined,
  metadata: {}
})

const validationEnabled = ref(false)

// Reset form when dialog opens
watch(() => props.visible, (visible) => {
  if (visible) {
    if (props.field) {
      form.value = JSON.parse(JSON.stringify(props.field))
      validationEnabled.value = !!props.field.validation
    } else {
      form.value = {
        name: '',
        label: '',
        type: 'string',
        mandatory: false,
        default_value: undefined,
        terminology_ref: undefined,
        template_ref: undefined,
        reference_type: undefined,
        target_templates: undefined,
        target_terminologies: undefined,
        version_strategy: undefined,
        array_item_type: undefined,
        array_terminology_ref: undefined,
        array_template_ref: undefined,
        validation: undefined,
        metadata: {}
      }
      validationEnabled.value = false
    }
  }
})

const isEdit = computed(() => !!props.field)
const dialogHeader = computed(() => isEdit.value ? 'Edit Field' : 'Add Field')

const terminologyOptions = computed(() =>
  props.terminologies.filter(t => t.name && t.code).map(t => ({
    label: `${t.name} (${t.code})`,
    value: t.terminology_id
  }))
)

const templateOptions = computed(() =>
  props.templates.filter(t => t.name && t.code).map(t => ({
    label: `${t.name} (${t.code})`,
    value: t.template_id
  }))
)

// Field types available for array items
const arrayItemTypes = computed(() =>
  FIELD_TYPES.filter(t => t.value !== 'array')
)

// Current type description for help text
const currentTypeDescription = computed(() => {
  const fieldType = FIELD_TYPES.find(t => t.value === form.value.type)
  return fieldType?.description || ''
})

// Determine which reference fields to show based on type
const showTerminologyRef = computed(() => form.value.type === 'term')
const showTemplateRef = computed(() => form.value.type === 'object')
const showReferenceConfig = computed(() => form.value.type === 'reference')
const showTargetTemplates = computed(() =>
  form.value.type === 'reference' && form.value.reference_type === 'document'
)
const showTargetTerminologies = computed(() =>
  form.value.type === 'reference' && form.value.reference_type === 'term'
)
const showArrayConfig = computed(() => form.value.type === 'array')
const showArrayTerminologyRef = computed(() =>
  form.value.type === 'array' && form.value.array_item_type === 'term'
)
const showArrayTemplateRef = computed(() =>
  form.value.type === 'array' && form.value.array_item_type === 'object'
)

// Options for target templates (by code for user-friendliness)
const targetTemplateOptions = computed(() =>
  props.templates.filter(t => t.name && t.code).map(t => ({
    label: `${t.name} (${t.code})`,
    value: t.code
  }))
)

// Options for target terminologies (by code)
const targetTerminologyOptions = computed(() =>
  props.terminologies.filter(t => t.name && t.code).map(t => ({
    label: `${t.name} (${t.code})`,
    value: t.code
  }))
)

// Whether to show the validation fieldset at all
const showValidationSection = computed(() => {
  // Don't show validation for types that have their own validation mechanisms
  const noValidationTypes = ['term', 'reference', 'object', 'boolean', 'date', 'datetime']
  if (noValidationTypes.includes(form.value.type)) return false
  if (form.value.type === 'array') {
    // For arrays, show validation if item type supports it
    return form.value.array_item_type && !noValidationTypes.includes(form.value.array_item_type)
  }
  return true
})

// Validation options based on field type
const showStringValidation = computed(() =>
  ['string'].includes(form.value.type) ||
  (form.value.type === 'array' && form.value.array_item_type === 'string')
)
const showNumericValidation = computed(() =>
  ['number', 'integer'].includes(form.value.type) ||
  (form.value.type === 'array' && ['number', 'integer'].includes(form.value.array_item_type || ''))
)

// Clear related fields when type changes
watch(() => form.value.type, (newType) => {
  if (newType !== 'term') {
    form.value.terminology_ref = undefined
  }
  if (newType !== 'object') {
    form.value.template_ref = undefined
  }
  if (newType !== 'reference') {
    form.value.reference_type = undefined
    form.value.target_templates = undefined
    form.value.target_terminologies = undefined
    form.value.version_strategy = undefined
  }
  if (newType !== 'array') {
    form.value.array_item_type = undefined
    form.value.array_terminology_ref = undefined
    form.value.array_template_ref = undefined
  }
  // Clear validation for types that don't support it
  const noValidationTypes = ['term', 'reference', 'object', 'boolean', 'date', 'datetime']
  if (noValidationTypes.includes(newType)) {
    form.value.validation = undefined
    validationEnabled.value = false
  }
})

// Clear target fields when reference type changes
watch(() => form.value.reference_type, (newRefType) => {
  if (newRefType !== 'document') {
    form.value.target_templates = undefined
  }
  if (newRefType !== 'term') {
    form.value.target_terminologies = undefined
  }
})

watch(() => form.value.array_item_type, (newType) => {
  if (newType !== 'term') {
    form.value.array_terminology_ref = undefined
  }
  if (newType !== 'object') {
    form.value.array_template_ref = undefined
  }
})

watch(validationEnabled, (enabled) => {
  if (enabled && !form.value.validation) {
    form.value.validation = {}
  } else if (!enabled) {
    form.value.validation = undefined
  }
})

function closeDialog() {
  emit('update:visible', false)
}

// Validation state
const isTerminologyRequired = computed(() => form.value.type === 'term')
const isTemplateRefRequired = computed(() => form.value.type === 'object')
const isArrayTerminologyRequired = computed(() =>
  form.value.type === 'array' && form.value.array_item_type === 'term'
)
const isArrayTemplateRequired = computed(() =>
  form.value.type === 'array' && form.value.array_item_type === 'object'
)

const terminologyMissing = computed(() =>
  isTerminologyRequired.value && !form.value.terminology_ref
)
const templateRefMissing = computed(() =>
  isTemplateRefRequired.value && !form.value.template_ref
)
const arrayTerminologyMissing = computed(() =>
  isArrayTerminologyRequired.value && !form.value.array_terminology_ref
)
const arrayTemplateRefMissing = computed(() =>
  isArrayTemplateRequired.value && !form.value.array_template_ref
)

const canSave = computed(() => {
  if (!form.value.name.trim()) return false
  if (!form.value.label.trim()) return false
  if (props.existingNames.includes(form.value.name)) return false
  if (terminologyMissing.value) return false
  if (templateRefMissing.value) return false
  if (isArrayTerminologyRequired.value && arrayTerminologyMissing.value) return false
  if (isArrayTemplateRequired.value && arrayTemplateRefMissing.value) return false
  if (form.value.type === 'array' && !form.value.array_item_type) return false
  return true
})

function save() {
  if (!canSave.value) {
    return
  }

  // Clean up empty validation object
  if (form.value.validation) {
    const v = form.value.validation
    const hasValidation =
      v.pattern ||
      v.min_length !== undefined ||
      v.max_length !== undefined ||
      v.minimum !== undefined ||
      v.maximum !== undefined ||
      (v.enum && v.enum.length > 0)

    if (!hasValidation) {
      form.value.validation = undefined
    }
  }

  emit('save', JSON.parse(JSON.stringify(form.value)))
}
</script>

<template>
  <Dialog
    :visible="visible"
    @update:visible="emit('update:visible', $event)"
    :header="dialogHeader"
    :style="{ width: '600px' }"
    modal
  >
    <div class="field-form">
      <!-- Basic Info -->
      <div class="form-section">
        <div class="form-row">
          <div class="form-field">
            <label for="name">Field Name *</label>
            <InputText
              id="name"
              v-model="form.name"
              placeholder="e.g., first_name"
              class="w-full"
              :class="{ 'p-invalid': existingNames.includes(form.name) }"
            />
            <small v-if="existingNames.includes(form.name)" class="p-error">
              Field name already exists
            </small>
            <small v-else>Used in data (snake_case recommended)</small>
          </div>

          <div class="form-field">
            <label for="label">Label *</label>
            <InputText
              id="label"
              v-model="form.label"
              placeholder="e.g., First Name"
              class="w-full"
            />
            <small>Human-readable display name</small>
          </div>
        </div>

        <div class="form-row">
          <div class="form-field">
            <label for="type">Type *</label>
            <Select
              id="type"
              v-model="form.type"
              :options="FIELD_TYPES"
              optionLabel="label"
              optionValue="value"
              class="w-full"
            >
              <template #option="{ option }">
                <div class="type-option">
                  <span class="type-label">{{ option.label }}</span>
                  <span class="type-description">{{ option.description }}</span>
                </div>
              </template>
            </Select>
            <small>{{ currentTypeDescription }}</small>
          </div>

          <div class="form-field checkbox-field">
            <Checkbox id="mandatory" v-model="form.mandatory" binary />
            <label for="mandatory">Required Field</label>
          </div>
        </div>
      </div>

      <!-- Type-specific references -->
      <div class="form-section" v-if="showTerminologyRef">
        <div class="form-field">
          <label for="terminology_ref">Terminology *</label>
          <Select
            id="terminology_ref"
            v-model="form.terminology_ref"
            :options="terminologyOptions"
            optionLabel="label"
            optionValue="value"
            placeholder="Select terminology (required)"
            :class="{ 'p-invalid': terminologyMissing }"
            class="w-full"
          />
          <small v-if="terminologyMissing" class="p-error">
            A terminology is required for term fields - this ensures controlled vocabulary
          </small>
          <small v-else-if="terminologyOptions.length === 0" class="p-error">
            No terminologies available. Create terminologies in the Terminologies section first.
          </small>
          <small v-else>Document values will be validated against this terminology</small>
        </div>
      </div>

      <div class="form-section" v-if="showTemplateRef">
        <div class="form-field">
          <label for="template_ref">Nested Template *</label>
          <Select
            id="template_ref"
            v-model="form.template_ref"
            :options="templateOptions"
            optionLabel="label"
            optionValue="value"
            placeholder="Select template (required)"
            :class="{ 'p-invalid': templateRefMissing }"
            class="w-full"
          />
          <small v-if="templateRefMissing" class="p-error">
            A template is required for object fields - this defines the nested structure
          </small>
          <small v-else-if="templateOptions.length === 0" class="p-error">
            No other templates available. Create the nested template first.
          </small>
          <small v-else>Nested objects will follow this template's structure</small>
        </div>
      </div>

      <!-- Reference configuration -->
      <Fieldset v-if="showReferenceConfig" legend="Reference Configuration">
        <div class="form-row">
          <div class="form-field">
            <label for="reference_type">Reference Type *</label>
            <Select
              id="reference_type"
              v-model="form.reference_type"
              :options="REFERENCE_TYPES"
              optionLabel="label"
              optionValue="value"
              placeholder="Select what this field references"
              class="w-full"
            >
              <template #option="{ option }">
                <div class="type-option">
                  <span class="type-label">{{ option.label }}</span>
                  <span class="type-description">{{ option.description }}</span>
                </div>
              </template>
            </Select>
          </div>

          <div class="form-field">
            <label for="version_strategy">Version Strategy</label>
            <Select
              id="version_strategy"
              v-model="form.version_strategy"
              :options="VERSION_STRATEGIES"
              optionLabel="label"
              optionValue="value"
              placeholder="Latest (default)"
              class="w-full"
            >
              <template #option="{ option }">
                <div class="type-option">
                  <span class="type-label">{{ option.label }}</span>
                  <span class="type-description">{{ option.description }}</span>
                </div>
              </template>
            </Select>
            <small>How to resolve reference versions over time</small>
          </div>
        </div>

        <div class="form-field" v-if="showTargetTemplates">
          <label for="target_templates">Target Templates *</label>
          <MultiSelect
            id="target_templates"
            v-model="form.target_templates"
            :options="targetTemplateOptions"
            optionLabel="label"
            optionValue="value"
            placeholder="Select allowed templates"
            class="w-full"
            display="chip"
          />
          <small v-if="targetTemplateOptions.length === 0" class="p-error">
            No templates available. Create a template first.
          </small>
          <small v-else>Documents referenced must conform to one of these templates</small>
        </div>

        <div class="form-field" v-if="showTargetTerminologies">
          <label for="target_terminologies">Target Terminologies *</label>
          <MultiSelect
            id="target_terminologies"
            v-model="form.target_terminologies"
            :options="targetTerminologyOptions"
            optionLabel="label"
            optionValue="value"
            placeholder="Select allowed terminologies"
            class="w-full"
            display="chip"
          />
          <small v-if="targetTerminologyOptions.length === 0" class="p-error">
            No terminologies available. Create a terminology first.
          </small>
          <small v-else>Terms referenced must belong to one of these terminologies</small>
        </div>
      </Fieldset>

      <!-- Array configuration -->
      <Fieldset v-if="showArrayConfig" legend="Array Configuration">
        <div class="form-row">
          <div class="form-field">
            <label for="array_item_type">Item Type *</label>
            <Select
              id="array_item_type"
              v-model="form.array_item_type"
              :options="arrayItemTypes"
              optionLabel="label"
              optionValue="value"
              placeholder="Select item type"
              class="w-full"
            />
          </div>
        </div>

        <div class="form-field" v-if="showArrayTerminologyRef">
          <label for="array_terminology_ref">Item Terminology *</label>
          <Select
            id="array_terminology_ref"
            v-model="form.array_terminology_ref"
            :options="terminologyOptions"
            optionLabel="label"
            optionValue="value"
            placeholder="Select terminology (required)"
            :class="{ 'p-invalid': arrayTerminologyMissing }"
            class="w-full"
          />
          <small v-if="arrayTerminologyMissing" class="p-error">
            A terminology is required for term array items
          </small>
          <small v-else-if="terminologyOptions.length === 0" class="p-error">
            No terminologies available. Create terminologies in the Terminologies section first.
          </small>
        </div>

        <div class="form-field" v-if="showArrayTemplateRef">
          <label for="array_template_ref">Item Template *</label>
          <Select
            id="array_template_ref"
            v-model="form.array_template_ref"
            :options="templateOptions"
            optionLabel="label"
            optionValue="value"
            placeholder="Select template (required)"
            :class="{ 'p-invalid': arrayTemplateRefMissing }"
            class="w-full"
          />
          <small v-if="arrayTemplateRefMissing" class="p-error">
            A template is required for object array items
          </small>
          <small v-else-if="templateOptions.length === 0" class="p-error">
            No other templates available. Create the nested template first.
          </small>
        </div>
      </Fieldset>

      <!-- Validation (only for string/numeric types) -->
      <Fieldset v-if="showValidationSection" legend="Validation">
        <div class="form-field checkbox-field">
          <Checkbox id="validation_enabled" v-model="validationEnabled" binary />
          <label for="validation_enabled">Enable field validation</label>
        </div>

        <template v-if="validationEnabled && form.validation">
          <div class="form-row" v-if="showStringValidation">
            <div class="form-field">
              <label for="pattern">Pattern (Regex)</label>
              <InputText
                id="pattern"
                v-model="form.validation.pattern"
                placeholder="e.g., ^[A-Z].*"
                class="w-full"
              />
            </div>
          </div>

          <div class="form-row" v-if="showStringValidation">
            <div class="form-field">
              <label for="min_length">Min Length</label>
              <InputNumber
                id="min_length"
                v-model="form.validation.min_length"
                :min="0"
                class="w-full"
              />
            </div>
            <div class="form-field">
              <label for="max_length">Max Length</label>
              <InputNumber
                id="max_length"
                v-model="form.validation.max_length"
                :min="0"
                class="w-full"
              />
            </div>
          </div>

          <div class="form-row" v-if="showNumericValidation">
            <div class="form-field">
              <label for="minimum">Minimum Value</label>
              <InputNumber
                id="minimum"
                v-model="form.validation.minimum"
                class="w-full"
              />
            </div>
            <div class="form-field">
              <label for="maximum">Maximum Value</label>
              <InputNumber
                id="maximum"
                v-model="form.validation.maximum"
                class="w-full"
              />
            </div>
          </div>
        </template>
      </Fieldset>
    </div>

    <template #footer>
      <Button label="Cancel" severity="secondary" text @click="closeDialog" />
      <Button
        :label="isEdit ? 'Save' : 'Add'"
        icon="pi pi-check"
        @click="save"
        :disabled="!canSave"
      />
    </template>
  </Dialog>
</template>

<style scoped>
.field-form {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.form-section {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.form-row {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 1rem;
}

.form-field {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.form-field label {
  font-weight: 500;
  font-size: 0.875rem;
}

.form-field small {
  color: var(--p-text-muted-color);
  font-size: 0.75rem;
}

.checkbox-field {
  flex-direction: row;
  align-items: center;
  gap: 0.5rem;
}

.checkbox-field label {
  font-weight: normal;
}

.w-full {
  width: 100%;
}

:deep(.p-fieldset) {
  margin-top: 0.5rem;
}

:deep(.p-fieldset-content) {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  padding-top: 0.75rem;
}

.type-option {
  display: flex;
  flex-direction: column;
  gap: 0.125rem;
}

.type-option .type-label {
  font-weight: 500;
}

.type-option .type-description {
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
}
</style>
