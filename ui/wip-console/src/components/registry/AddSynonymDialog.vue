<script setup lang="ts">
import { ref, onMounted } from 'vue'
import Dialog from 'primevue/dialog'
import Button from 'primevue/button'
import Dropdown from 'primevue/dropdown'
import InputText from 'primevue/inputtext'
import { registryClient, type Namespace } from '@/api/client'
import { useUiStore } from '@/stores'

const props = defineProps<{
  visible: boolean
  entryId: string
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  added: []
}>()

const uiStore = useUiStore()
const submitting = ref(false)
const namespaces = ref<Namespace[]>([])
const loadingNamespaces = ref(false)

// Form state
const selectedNamespace = ref<string>('')
const selectedEntityType = ref<string>('terms')
const keyPairs = ref<Array<{ key: string; value: string }>>([{ key: '', value: '' }])

const entityTypeOptions = [
  { label: 'Terminologies', value: 'terminologies' },
  { label: 'Terms', value: 'terms' },
  { label: 'Templates', value: 'templates' },
  { label: 'Documents', value: 'documents' },
  { label: 'Files', value: 'files' }
]

onMounted(async () => {
  loadingNamespaces.value = true
  try {
    namespaces.value = await registryClient.listNamespaces()
  } catch {
    // Silently fail
  } finally {
    loadingNamespaces.value = false
  }
})

function addKeyPair() {
  keyPairs.value.push({ key: '', value: '' })
}

function removeKeyPair(index: number) {
  if (keyPairs.value.length > 1) {
    keyPairs.value.splice(index, 1)
  }
}

function resetForm() {
  selectedNamespace.value = ''
  selectedEntityType.value = 'terms'
  keyPairs.value = [{ key: '', value: '' }]
}

function closeDialog() {
  emit('update:visible', false)
  resetForm()
}

const isValid = () => {
  return (
    selectedNamespace.value &&
    selectedEntityType.value &&
    keyPairs.value.some(p => p.key.trim() && p.value.trim())
  )
}

async function submit() {
  if (!isValid()) return

  const compositeKey: Record<string, string> = {}
  for (const pair of keyPairs.value) {
    if (pair.key.trim() && pair.value.trim()) {
      compositeKey[pair.key.trim()] = pair.value.trim()
    }
  }

  submitting.value = true
  try {
    const result = await registryClient.addSynonym({
      target_id: props.entryId,
      synonym_namespace: selectedNamespace.value,
      synonym_entity_type: selectedEntityType.value,
      synonym_composite_key: compositeKey,
    })

    if (result.status === 'added') {
      uiStore.showSuccess('Synonym added successfully')
      emit('added')
      closeDialog()
    } else {
      uiStore.showError('Failed to add synonym', result.error || result.status)
    }
  } catch (e) {
    uiStore.showError('Failed to add synonym', (e as Error).message)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <Dialog
    :visible="visible"
    @update:visible="emit('update:visible', $event)"
    header="Add Synonym"
    :modal="true"
    :style="{ width: '500px' }"
  >
    <div class="form-content">
      <div class="field">
        <label>Namespace</label>
        <Dropdown
          v-model="selectedNamespace"
          :options="namespaces.map(n => ({ label: n.prefix, value: n.prefix }))"
          optionLabel="label"
          optionValue="value"
          placeholder="Select namespace"
          :loading="loadingNamespaces"
          class="w-full"
        />
      </div>

      <div class="field">
        <label>Entity Type</label>
        <Dropdown
          v-model="selectedEntityType"
          :options="entityTypeOptions"
          optionLabel="label"
          optionValue="value"
          placeholder="Select entity type"
          class="w-full"
        />
      </div>

      <div class="field">
        <label>Composite Key</label>
        <div class="key-pairs">
          <div v-for="(pair, index) in keyPairs" :key="index" class="key-pair-row">
            <InputText
              v-model="pair.key"
              placeholder="Key name"
              class="key-input"
            />
            <InputText
              v-model="pair.value"
              placeholder="Value"
              class="value-input"
            />
            <Button
              icon="pi pi-times"
              text
              rounded
              severity="danger"
              size="small"
              :disabled="keyPairs.length <= 1"
              @click="removeKeyPair(index)"
            />
          </div>
          <Button
            icon="pi pi-plus"
            label="Add Field"
            text
            size="small"
            @click="addKeyPair"
          />
        </div>
      </div>
    </div>

    <template #footer>
      <Button
        label="Cancel"
        severity="secondary"
        text
        @click="closeDialog"
      />
      <Button
        label="Add Synonym"
        icon="pi pi-plus"
        :loading="submitting"
        :disabled="!isValid()"
        @click="submit"
      />
    </template>
  </Dialog>
</template>

<style scoped>
.form-content {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
}

.field label {
  font-size: 0.8125rem;
  font-weight: 600;
  color: var(--p-text-muted-color);
}

.key-pairs {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.key-pair-row {
  display: flex;
  gap: 0.5rem;
  align-items: center;
}

.key-input {
  flex: 0 0 40%;
}

.value-input {
  flex: 1;
}

.w-full {
  width: 100%;
}
</style>
