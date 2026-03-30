<script setup lang="ts">
import { ref, watch } from 'vue'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'
import Button from 'primevue/button'
import { useUiStore, useNamespaceStore } from '@/stores'
import { defStoreClient } from '@/api/client'

const props = defineProps<{
  visible: boolean
  defaultSourceTermId?: string
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  created: []
}>()

const uiStore = useUiStore()
const namespaceStore = useNamespaceStore()
const submitting = ref(false)

const form = ref({
  source_term_id: '',
  target_term_id: '',
  relationship_type: 'is_a',
})

const relationshipTypes = [
  { label: 'is_a (subsumption)', value: 'is_a' },
  { label: 'part_of', value: 'part_of' },
  { label: 'has_part', value: 'has_part' },
  { label: 'maps_to', value: 'maps_to' },
  { label: 'related_to', value: 'related_to' },
  { label: 'finding_site', value: 'finding_site' },
  { label: 'causative_agent', value: 'causative_agent' },
]

watch(() => props.visible, (visible) => {
  if (visible) {
    form.value = {
      source_term_id: props.defaultSourceTermId || '',
      target_term_id: '',
      relationship_type: 'is_a',
    }
  }
})

async function submit() {
  if (!form.value.source_term_id || !form.value.target_term_id) {
    uiStore.showError('Validation', 'Source and target term IDs are required')
    return
  }

  submitting.value = true
  try {
    const result = await defStoreClient.createRelationships([{
      source_term_id: form.value.source_term_id,
      target_term_id: form.value.target_term_id,
      relationship_type: form.value.relationship_type,
    }], namespaceStore.currentNamespaceParam)

    if (result.failed > 0) {
      const error = result.results[0]?.error || 'Unknown error'
      uiStore.showError('Create failed', error)
    } else {
      uiStore.showSuccess('Relationship created')
      emit('created')
    }
  } catch (e) {
    uiStore.showError('Create failed', (e as Error).message)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <Dialog
    :visible="visible"
    @update:visible="emit('update:visible', $event)"
    header="Create Relationship"
    modal
    :style="{ width: '500px' }"
  >
    <form @submit.prevent="submit" class="form">
      <div class="field">
        <label for="source">Source Term ID</label>
        <InputText
          id="source"
          v-model="form.source_term_id"
          placeholder="e.g. T-000123"
          class="w-full"
          :disabled="!!props.defaultSourceTermId"
        />
      </div>

      <div class="field">
        <label for="type">Relationship Type</label>
        <Select
          id="type"
          v-model="form.relationship_type"
          :options="relationshipTypes"
          option-label="label"
          option-value="value"
          class="w-full"
        />
      </div>

      <div class="field">
        <label for="target">Target Term ID</label>
        <InputText
          id="target"
          v-model="form.target_term_id"
          placeholder="e.g. T-000456"
          class="w-full"
        />
      </div>

      <div class="hint">
        <i class="pi pi-info-circle"></i>
        <span>
          Reads as: Source <strong>{{ form.relationship_type }}</strong> Target.
          For example: "Viral pneumonia" <strong>is_a</strong> "Pneumonia".
        </span>
      </div>
    </form>

    <template #footer>
      <Button
        label="Cancel"
        severity="secondary"
        text
        @click="emit('update:visible', false)"
      />
      <Button
        label="Create"
        icon="pi pi-plus"
        :loading="submitting"
        @click="submit"
      />
    </template>
  </Dialog>
</template>

<style scoped>
.form {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.field label {
  font-weight: 500;
  font-size: 0.875rem;
}

.w-full {
  width: 100%;
}

.hint {
  display: flex;
  gap: 0.5rem;
  align-items: flex-start;
  font-size: 0.8rem;
  color: var(--p-text-muted-color);
  background: var(--p-surface-50);
  padding: 0.75rem;
  border-radius: var(--p-border-radius);
}

.hint i {
  margin-top: 0.125rem;
  flex-shrink: 0;
}
</style>
