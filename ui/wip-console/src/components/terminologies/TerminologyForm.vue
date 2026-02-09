<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Textarea from 'primevue/textarea'
import Button from 'primevue/button'
import { useTerminologyStore, useUiStore } from '@/stores'
import type { Terminology, CreateTerminologyRequest, UpdateTerminologyRequest } from '@/types'

const props = defineProps<{
  visible: boolean
  terminology?: Terminology
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  created: []
  updated: []
}>()

const terminologyStore = useTerminologyStore()
const uiStore = useUiStore()

const isEdit = computed(() => !!props.terminology)
const dialogTitle = computed(() => isEdit.value ? 'Edit Terminology' : 'Create Terminology')

const form = ref({
  code: '',
  name: '',
  description: '',
  case_sensitive: false,
  allow_multiple: false,
  extensible: false,
  source: '',
  source_url: '',
  version: '',
  language: 'en'
})

const loading = ref(false)
const errors = ref<Record<string, string>>({})

watch(
  () => props.visible,
  (visible) => {
    if (visible) {
      if (props.terminology) {
        form.value = {
          code: props.terminology.code,
          name: props.terminology.name,
          description: props.terminology.description || '',
          case_sensitive: props.terminology.case_sensitive,
          allow_multiple: props.terminology.allow_multiple,
          extensible: props.terminology.extensible,
          source: props.terminology.metadata?.source || '',
          source_url: props.terminology.metadata?.source_url || '',
          version: props.terminology.metadata?.version || '',
          language: props.terminology.metadata?.language || 'en'
        }
      } else {
        resetForm()
      }
      errors.value = {}
    }
  }
)

function resetForm() {
  form.value = {
    code: '',
    name: '',
    description: '',
    case_sensitive: false,
    allow_multiple: false,
    extensible: false,
    source: '',
    source_url: '',
    version: '',
    language: 'en'
  }
}

function validate(): boolean {
  errors.value = {}

  if (!form.value.code.trim()) {
    errors.value.code = 'Code is required'
  } else if (!/^[A-Z0-9_]+$/.test(form.value.code)) {
    errors.value.code = 'Code must be uppercase letters, numbers, and underscores only'
  }

  if (!form.value.name.trim()) {
    errors.value.name = 'Name is required'
  }

  return Object.keys(errors.value).length === 0
}

async function submit() {
  if (!validate()) return

  loading.value = true
  try {
    if (isEdit.value && props.terminology) {
      const updateData: UpdateTerminologyRequest = {
        code: form.value.code !== props.terminology.code ? form.value.code : undefined,
        name: form.value.name,
        description: form.value.description || undefined,
        case_sensitive: form.value.case_sensitive,
        allow_multiple: form.value.allow_multiple,
        extensible: form.value.extensible,
        metadata: {
          source: form.value.source || undefined,
          source_url: form.value.source_url || undefined,
          version: form.value.version || undefined,
          language: form.value.language
        }
      }
      await terminologyStore.updateTerminology(props.terminology.terminology_id, updateData)
      uiStore.showSuccess('Terminology Updated', `"${form.value.name}" has been updated`)
      emit('updated')
    } else {
      const createData: CreateTerminologyRequest = {
        code: form.value.code,
        name: form.value.name,
        description: form.value.description || undefined,
        case_sensitive: form.value.case_sensitive,
        allow_multiple: form.value.allow_multiple,
        extensible: form.value.extensible,
        metadata: {
          source: form.value.source || undefined,
          source_url: form.value.source_url || undefined,
          version: form.value.version || undefined,
          language: form.value.language
        }
      }
      await terminologyStore.createTerminology(createData)
      uiStore.showSuccess('Terminology Created', `"${form.value.name}" has been created`)
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
</script>

<template>
  <Dialog
    :visible="visible"
    @update:visible="$emit('update:visible', $event)"
    :header="dialogTitle"
    :style="{ width: '550px' }"
    modal
  >
    <form @submit.prevent="submit" class="terminology-form">
      <div class="form-row">
        <div class="form-field">
          <label for="code">Code *</label>
          <InputText
            id="code"
            v-model="form.code"
            :class="{ 'p-invalid': errors.code }"
            placeholder="e.g., DOC_STATUS"
            :disabled="isEdit"
          />
          <small v-if="errors.code" class="p-error">{{ errors.code }}</small>
          <small v-else class="help-text">Machine identifier (e.g., DOC_STATUS). Uppercase letters, numbers, underscores.</small>
        </div>
      </div>

      <div class="form-row">
        <div class="form-field">
          <label for="name">Name *</label>
          <InputText
            id="name"
            v-model="form.name"
            :class="{ 'p-invalid': errors.name }"
            placeholder="e.g., Document Status"
          />
          <small v-if="errors.name" class="p-error">{{ errors.name }}</small>
          <small v-else class="help-text">Human-readable display name</small>
        </div>
      </div>

      <div class="form-row">
        <div class="form-field">
          <label for="description">Description</label>
          <Textarea
            id="description"
            v-model="form.description"
            rows="3"
            placeholder="Optional description..."
          />
        </div>
      </div>

      <!-- case_sensitive, allow_multiple, extensible flags removed — not enforced by API -->

      <div class="form-section">
        <h4>Metadata</h4>
        <div class="form-row two-cols">
          <div class="form-field">
            <label for="source">Source</label>
            <InputText
              id="source"
              v-model="form.source"
              placeholder="e.g., ISO 3166"
            />
          </div>
          <div class="form-field">
            <label for="version">Version</label>
            <InputText
              id="version"
              v-model="form.version"
              placeholder="e.g., 2024.1"
            />
            <small class="help-text">User-supplied version label (e.g., ISO edition). Not system-managed.</small>
          </div>
        </div>

        <div class="form-row two-cols">
          <div class="form-field">
            <label for="source_url">Source URL</label>
            <InputText
              id="source_url"
              v-model="form.source_url"
              placeholder="https://..."
            />
          </div>
          <div class="form-field">
            <label for="language">Language</label>
            <InputText
              id="language"
              v-model="form.language"
              placeholder="e.g., en"
            />
          </div>
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
.terminology-form {
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

.checkboxes {
  display: flex;
  gap: 1.5rem;
  flex-wrap: wrap;
}

.checkbox-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.checkbox-item label {
  font-size: 0.875rem;
  cursor: pointer;
}

.form-section {
  border-top: 1px solid var(--p-surface-border);
  padding-top: 1rem;
  margin-top: 0.5rem;
}

.form-section h4 {
  margin: 0 0 1rem 0;
  font-size: 0.875rem;
  color: var(--p-text-muted-color);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
</style>
