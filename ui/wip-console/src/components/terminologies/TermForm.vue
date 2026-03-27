<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import InputNumber from 'primevue/inputnumber'
import Textarea from 'primevue/textarea'
import Chips from 'primevue/chips'
import Select from 'primevue/select'
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
  value: '',
  aliases: [] as string[],
  label: '',
  description: '',
  sort_order: 0,
  parent_term_id: null as string | null
})

// Parent term options: all terms in the terminology except the current term (when editing)
const parentTermOptions = computed(() => {
  return termStore.terms
    .filter(t => t.status === 'active' && (!props.term || t.term_id !== props.term.term_id))
    .map(t => ({ label: t.label || t.value, value: t.term_id }))
    .sort((a, b) => a.label.localeCompare(b.label))
})

const loading = ref(false)
const errors = ref<Record<string, string>>({})

watch(
  () => props.visible,
  (visible) => {
    if (visible) {
      if (props.term) {
        form.value = {
          value: props.term.value,
          aliases: props.term.aliases || [],
          label: props.term.label || '',
          description: props.term.description || '',
          sort_order: props.term.sort_order,
          parent_term_id: props.term.parent_term_id || null
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
    value: '',
    aliases: [],
    label: '',
    description: '',
    sort_order: maxOrder + 1,
    parent_term_id: null
  }
}

function validate(): boolean {
  errors.value = {}

  if (!form.value.value.trim()) {
    errors.value.value = 'Value is required'
  }

  return Object.keys(errors.value).length === 0
}

async function submit() {
  if (!validate()) return

  loading.value = true
  try {
    if (isEdit.value && props.term) {
      const updateData: UpdateTermRequest = {
        value: form.value.value,
        aliases: form.value.aliases,
        label: form.value.label || undefined,
        description: form.value.description || undefined,
        sort_order: form.value.sort_order,
        parent_term_id: form.value.parent_term_id || undefined
      }
      await termStore.updateTerm(props.term.term_id, updateData)
      uiStore.showSuccess('Term Updated', `"${form.value.label || form.value.value}" has been updated`)
      emit('updated')
    } else {
      const createData: CreateTermRequest = {
        value: form.value.value,
        aliases: form.value.aliases.length > 0 ? form.value.aliases : undefined,
        label: form.value.label || undefined,
        description: form.value.description || undefined,
        sort_order: form.value.sort_order,
        parent_term_id: form.value.parent_term_id || undefined
      }
      await termStore.createTerm(props.terminologyId, createData)
      uiStore.showSuccess('Term Created', `"${form.value.label || form.value.value}" has been created`)
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

// Auto-fill label from value (Title Case)
function onValueBlur() {
  if (!isEdit.value && form.value.value && !form.value.label) {
    // Convert value to Title Case for label suggestion
    form.value.label = form.value.value
      .split(/[\s_-]+/)
      .map(word => word.charAt(0).toUpperCase() + word.slice(1))
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
      <div class="form-row two-cols">
        <div class="form-field">
          <label for="value">Value *</label>
          <InputText
            id="value"
            v-model="form.value"
            :class="{ 'p-invalid': errors.value }"
            placeholder="e.g., Approved"
            @blur="onValueBlur"
          />
          <small v-if="errors.value" class="p-error">{{ errors.value }}</small>
          <small v-else class="help-text">The unique value stored in documents. Used for matching and validation.</small>
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
          <label for="label">Label</label>
          <InputText
            id="label"
            v-model="form.label"
            placeholder="Defaults to value if empty"
          />
          <small class="help-text">Human-readable display text shown in dropdowns and forms</small>
        </div>
      </div>

      <div class="form-row">
        <div class="form-field">
          <label for="aliases">Aliases</label>
          <Chips
            id="aliases"
            v-model="form.aliases"
            placeholder="Type and press Enter to add..."
            :allow-duplicate="false"
            separator=","
            :addOnBlur="true"
          />
          <small class="help-text">Alternative values that resolve to this term (e.g., Mr., MR, mr)</small>
        </div>
      </div>

      <div class="form-row">
        <div class="form-field">
          <label for="parent_term">Parent Term</label>
          <Select
            id="parent_term"
            v-model="form.parent_term_id"
            :options="parentTermOptions"
            optionLabel="label"
            optionValue="value"
            placeholder="None (root term)"
            :showClear="true"
            filter
            class="w-full"
          />
          <small class="help-text">Optional parent for hierarchical term structures</small>
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
