<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import InputNumber from 'primevue/inputnumber'
import Select from 'primevue/select'
import Button from 'primevue/button'
import Fieldset from 'primevue/fieldset'
import Chips from 'primevue/chips'
import type { ValidationRule, FieldDefinition } from '@/types'
import { RULE_TYPES, CONDITION_OPERATORS } from '@/types'

const props = defineProps<{
  visible: boolean
  rule: ValidationRule | null
  fields: FieldDefinition[]
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  save: [rule: ValidationRule]
}>()

const form = ref<ValidationRule>({
  type: 'conditional_required',
  description: undefined,
  conditions: [],
  target_field: undefined,
  target_fields: undefined,
  required: undefined,
  allowed_values: undefined,
  pattern: undefined,
  minimum: undefined,
  maximum: undefined,
  error_message: undefined
})

// Reset form when dialog opens
watch(() => props.visible, (visible) => {
  if (visible) {
    if (props.rule) {
      form.value = JSON.parse(JSON.stringify(props.rule))
    } else {
      form.value = {
        type: 'conditional_required',
        description: undefined,
        conditions: [],
        target_field: undefined,
        target_fields: undefined,
        required: undefined,
        allowed_values: undefined,
        pattern: undefined,
        minimum: undefined,
        maximum: undefined,
        error_message: undefined
      }
    }
  }
})

const isEdit = computed(() => !!props.rule)
const dialogHeader = computed(() => isEdit.value ? 'Edit Rule' : 'Add Rule')

const fieldOptions = computed(() =>
  props.fields.map(f => ({
    label: `${f.label} (${f.name})`,
    value: f.name
  }))
)

// Determine which fields to show based on rule type
const showTargetField = computed(() =>
  ['conditional_required', 'conditional_value', 'dependency', 'pattern', 'range'].includes(form.value.type)
)
const showTargetFields = computed(() => form.value.type === 'mutual_exclusion')
const showConditions = computed(() =>
  ['conditional_required', 'conditional_value', 'dependency'].includes(form.value.type)
)
const showRequired = computed(() => form.value.type === 'conditional_required')
const showAllowedValues = computed(() => form.value.type === 'conditional_value')
const showPattern = computed(() => form.value.type === 'pattern')
const showRange = computed(() => form.value.type === 'range')

// Clear irrelevant fields when type changes
watch(() => form.value.type, () => {
  if (!showTargetField.value) {
    form.value.target_field = undefined
  }
  if (!showTargetFields.value) {
    form.value.target_fields = undefined
  }
  if (!showConditions.value) {
    form.value.conditions = []
  }
  if (!showRequired.value) {
    form.value.required = undefined
  }
  if (!showAllowedValues.value) {
    form.value.allowed_values = undefined
  }
  if (!showPattern.value) {
    form.value.pattern = undefined
  }
  if (!showRange.value) {
    form.value.minimum = undefined
    form.value.maximum = undefined
  }
})

function addCondition() {
  form.value.conditions.push({
    field: '',
    operator: 'equals',
    value: undefined
  })
}

function removeCondition(index: number) {
  form.value.conditions.splice(index, 1)
}

function closeDialog() {
  emit('update:visible', false)
}

function save() {
  // Basic validation
  if (showTargetField.value && !form.value.target_field) {
    return
  }
  if (showTargetFields.value && (!form.value.target_fields || form.value.target_fields.length < 2)) {
    return
  }

  emit('save', JSON.parse(JSON.stringify(form.value)))
}
</script>

<template>
  <Dialog
    :visible="visible"
    @update:visible="emit('update:visible', $event)"
    :header="dialogHeader"
    :style="{ width: '650px' }"
    modal
  >
    <div class="rule-form">
      <!-- Rule Type -->
      <div class="form-section">
        <div class="form-field">
          <label for="type">Rule Type *</label>
          <Select
            id="type"
            v-model="form.type"
            :options="RULE_TYPES"
            optionLabel="label"
            optionValue="value"
            class="w-full"
          >
            <template #option="{ option }">
              <div class="rule-type-option">
                <span class="label">{{ option.label }}</span>
                <span class="description">{{ option.description }}</span>
              </div>
            </template>
          </Select>
        </div>

        <div class="form-field">
          <label for="description">Description</label>
          <InputText
            id="description"
            v-model="form.description"
            placeholder="Human-readable description of this rule"
            class="w-full"
          />
        </div>
      </div>

      <!-- Target Field(s) -->
      <div class="form-section" v-if="showTargetField">
        <div class="form-field">
          <label for="target_field">Target Field *</label>
          <Select
            id="target_field"
            v-model="form.target_field"
            :options="fieldOptions"
            optionLabel="label"
            optionValue="value"
            placeholder="Select field"
            class="w-full"
          />
        </div>
      </div>

      <div class="form-section" v-if="showTargetFields">
        <div class="form-field">
          <label for="target_fields">Mutually Exclusive Fields *</label>
          <Select
            id="target_fields"
            v-model="form.target_fields"
            :options="fieldOptions"
            optionLabel="label"
            optionValue="value"
            placeholder="Select at least 2 fields"
            multiple
            class="w-full"
          />
          <small>Only one of these fields can have a value at a time</small>
        </div>
      </div>

      <!-- Conditions -->
      <Fieldset v-if="showConditions" legend="Conditions">
        <div class="conditions-list">
          <div
            v-for="(condition, index) in form.conditions"
            :key="index"
            class="condition-row"
          >
            <Select
              v-model="condition.field"
              :options="fieldOptions"
              optionLabel="label"
              optionValue="value"
              placeholder="Field"
              class="condition-field"
            />
            <Select
              v-model="condition.operator"
              :options="CONDITION_OPERATORS"
              optionLabel="label"
              optionValue="value"
              class="condition-operator"
            />
            <InputText
              :modelValue="String(condition.value ?? '')"
              @update:modelValue="condition.value = $event"
              placeholder="Value"
              class="condition-value"
              v-if="!['exists', 'not_exists'].includes(condition.operator)"
            />
            <Button
              icon="pi pi-times"
              severity="danger"
              text
              rounded
              size="small"
              @click="removeCondition(index)"
            />
          </div>
          <Button
            label="Add Condition"
            icon="pi pi-plus"
            severity="secondary"
            size="small"
            @click="addCondition"
          />
        </div>
      </Fieldset>

      <!-- Rule-specific options -->
      <div class="form-section" v-if="showRequired">
        <div class="form-field">
          <label>Required when conditions are met?</label>
          <Select
            v-model="form.required"
            :options="[{ label: 'Yes', value: true }, { label: 'No', value: false }]"
            optionLabel="label"
            optionValue="value"
            class="w-full"
          />
        </div>
      </div>

      <div class="form-section" v-if="showAllowedValues">
        <div class="form-field">
          <label for="allowed_values">Allowed Values</label>
          <Chips
            id="allowed_values"
            :modelValue="(form.allowed_values as string[]) ?? []"
            @update:modelValue="form.allowed_values = $event"
            placeholder="Add allowed values"
            class="w-full"
          />
          <small>Values allowed when conditions are met</small>
        </div>
      </div>

      <div class="form-section" v-if="showPattern">
        <div class="form-field">
          <label for="pattern">Pattern (Regex) *</label>
          <InputText
            id="pattern"
            v-model="form.pattern"
            placeholder="e.g., ^[A-Z][a-z]+$"
            class="w-full"
          />
        </div>
      </div>

      <div class="form-section" v-if="showRange">
        <div class="form-row">
          <div class="form-field">
            <label for="minimum">Minimum</label>
            <InputNumber
              id="minimum"
              v-model="form.minimum"
              class="w-full"
            />
          </div>
          <div class="form-field">
            <label for="maximum">Maximum</label>
            <InputNumber
              id="maximum"
              v-model="form.maximum"
              class="w-full"
            />
          </div>
        </div>
      </div>

      <!-- Error Message -->
      <div class="form-section">
        <div class="form-field">
          <label for="error_message">Custom Error Message</label>
          <InputText
            id="error_message"
            v-model="form.error_message"
            placeholder="Error shown when rule validation fails"
            class="w-full"
          />
        </div>
      </div>
    </div>

    <template #footer>
      <Button label="Cancel" severity="secondary" text @click="closeDialog" />
      <Button
        :label="isEdit ? 'Save' : 'Add'"
        icon="pi pi-check"
        @click="save"
      />
    </template>
  </Dialog>
</template>

<style scoped>
.rule-form {
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

.rule-type-option {
  display: flex;
  flex-direction: column;
  gap: 0.125rem;
}

.rule-type-option .label {
  font-weight: 500;
}

.rule-type-option .description {
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
}

.conditions-list {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.condition-row {
  display: flex;
  gap: 0.5rem;
  align-items: center;
}

.condition-field {
  flex: 2;
}

.condition-operator {
  flex: 1;
}

.condition-value {
  flex: 1;
}

.w-full {
  width: 100%;
}

:deep(.p-fieldset) {
  margin-top: 0.5rem;
}

:deep(.p-fieldset-content) {
  padding-top: 0.75rem;
}
</style>
