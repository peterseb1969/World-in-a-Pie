<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import InputNumber from 'primevue/inputnumber'
import Textarea from 'primevue/textarea'
import Button from 'primevue/button'
import { useTermStore, useUiStore } from '@/stores'
import type { Term, CreateTermRequest, UpdateTermRequest } from '@/types'

const props = defineProps<{
  visible: boolean
  terminologyId: string
  term?: Term
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  created: []
  updated: []
}>()

const termStore = useTermStore()
const uiStore = useUiStore()

const isEdit = computed(() => !!props.term)
const dialogTitle = computed(() => isEdit.value ? 'Edit Term' : 'Create Term')

const form = ref({
  code: '',
  value: '',
  label: '',
  description: '',
  sort_order: 0
})

const loading = ref(false)
const errors = ref<Record<string, string>>({})

watch(
  () => props.visible,
  (visible) => {
    if (visible) {
      if (props.term) {
        form.value = {
          code: props.term.code,
          value: props.term.value,
          label: props.term.label,
          description: props.term.description || '',
          sort_order: props.term.sort_order
        }
      } else {
        resetForm()
      }
      errors.value = {}
    }
  }
)

function resetForm() {
  // Calculate next sort order
  const maxOrder = termStore.terms.reduce((max, t) => Math.max(max, t.sort_order), -1)
  form.value = {
    code: '',
    value: '',
    label: '',
    description: '',
    sort_order: maxOrder + 1
  }
}

function validate(): boolean {
  errors.value = {}

  if (!form.value.code.trim()) {
    errors.value.code = 'Code is required'
  } else if (!/^[A-Z0-9_]+$/.test(form.value.code)) {
    errors.value.code = 'Code must be uppercase letters, numbers, and underscores only'
  }

  if (!form.value.value.trim()) {
    errors.value.value = 'Value is required'
  }

  if (!form.value.label.trim()) {
    errors.value.label = 'Label is required'
  }

  return Object.keys(errors.value).length === 0
}

async function submit() {
  if (!validate()) return

  loading.value = true
  try {
    if (isEdit.value && props.term) {
      const updateData: UpdateTermRequest = {
        code: form.value.code !== props.term.code ? form.value.code : undefined,
        value: form.value.value,
        label: form.value.label,
        description: form.value.description || undefined,
        sort_order: form.value.sort_order
      }
      await termStore.updateTerm(props.term.term_id, updateData)
      uiStore.showSuccess('Term Updated', `"${form.value.label}" has been updated`)
      emit('updated')
    } else {
      const createData: CreateTermRequest = {
        code: form.value.code,
        value: form.value.value,
        label: form.value.label,
        description: form.value.description || undefined,
        sort_order: form.value.sort_order
      }
      await termStore.createTerm(props.terminologyId, createData)
      uiStore.showSuccess('Term Created', `"${form.value.label}" has been created`)
      emit('created')
    }
    emit('update:visible', false)
  } catch (e) {
    uiStore.showError('Operation Failed', (e as Error).message)
  } finally {
    loading.value = false
  }
}

function cancel() {
  emit('update:visible', false)
}

// Auto-fill value and label from code
function onCodeChange() {
  if (!isEdit.value && form.value.code && !form.value.value) {
    form.value.value = form.value.code.toLowerCase()
  }
  if (!isEdit.value && form.value.code && !form.value.label) {
    // Convert SNAKE_CASE to Title Case
    form.value.label = form.value.code
      .split('_')
      .map(word => word.charAt(0) + word.slice(1).toLowerCase())
      .join(' ')
  }
}
</script>

<template>
  <Dialog
    :visible="visible"
    @update:visible="$emit('update:visible', $event)"
    :header="dialogTitle"
    :style="{ width: '500px' }"
    modal
  >
    <form @submit.prevent="submit" class="term-form">
      <div class="form-row">
        <div class="form-field">
          <label for="code">Code *</label>
          <InputText
            id="code"
            v-model="form.code"
            :class="{ 'p-invalid': errors.code }"
            placeholder="e.g., APPROVED"
            @blur="onCodeChange"
          />
          <small v-if="errors.code" class="p-error">{{ errors.code }}</small>
          <small v-else class="help-text">Uppercase letters, numbers, underscores</small>
        </div>
      </div>

      <div class="form-row two-cols">
        <div class="form-field">
          <label for="value">Value *</label>
          <InputText
            id="value"
            v-model="form.value"
            :class="{ 'p-invalid': errors.value }"
            placeholder="e.g., approved"
          />
          <small v-if="errors.value" class="p-error">{{ errors.value }}</small>
          <small v-else class="help-text">Stored in documents</small>
        </div>

        <div class="form-field">
          <label for="sort_order">Sort Order</label>
          <InputNumber
            id="sort_order"
            v-model="form.sort_order"
            :min="0"
            show-buttons
          />
        </div>
      </div>

      <div class="form-row">
        <div class="form-field">
          <label for="label">Label *</label>
          <InputText
            id="label"
            v-model="form.label"
            :class="{ 'p-invalid': errors.label }"
            placeholder="e.g., Approved"
          />
          <small v-if="errors.label" class="p-error">{{ errors.label }}</small>
          <small v-else class="help-text">Display text in UI</small>
        </div>
      </div>

      <div class="form-row">
        <div class="form-field">
          <label for="description">Description</label>
          <Textarea
            id="description"
            v-model="form.description"
            rows="2"
            placeholder="Optional description..."
          />
        </div>
      </div>
    </form>

    <template #footer>
      <Button
        label="Cancel"
        severity="secondary"
        text
        @click="cancel"
        :disabled="loading"
      />
      <Button
        :label="isEdit ? 'Update' : 'Create'"
        @click="submit"
        :loading="loading"
      />
    </template>
  </Dialog>
</template>

<style scoped>
.term-form {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.form-row {
  display: flex;
  gap: 1rem;
}

.form-row.two-cols {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1rem;
}

.form-field {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  flex: 1;
}

.form-field label {
  font-weight: 500;
  font-size: 0.875rem;
}

.form-field input,
.form-field textarea {
  width: 100%;
}

.help-text {
  color: var(--p-text-muted-color);
  font-size: 0.75rem;
}
</style>
