<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import Dialog from 'primevue/dialog'
import Textarea from 'primevue/textarea'
import Dropdown from 'primevue/dropdown'
import Button from 'primevue/button'
import { useTermStore, useUiStore } from '@/stores'
import type { Term } from '@/types'

const props = defineProps<{
  visible: boolean
  term: Term
  terms: Term[]
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  deprecated: []
}>()

const termStore = useTermStore()
const uiStore = useUiStore()

const reason = ref('')
const replacedByTermId = ref<string | null>(null)
const loading = ref(false)
const error = ref('')

const replacementOptions = computed(() => {
  return props.terms
    .filter(t => t.term_id !== props.term.term_id && t.status === 'active')
    .map(t => ({
      label: `${t.label || t.value} (${t.value})`,
      value: t.term_id
    }))
})

watch(
  () => props.visible,
  (visible) => {
    if (visible) {
      reason.value = ''
      replacedByTermId.value = null
      error.value = ''
    }
  }
)

async function submit() {
  if (!reason.value.trim()) {
    error.value = 'Please provide a reason for deprecation'
    return
  }

  loading.value = true
  try {
    await termStore.deprecateTerm(props.term.term_id, {
      reason: reason.value,
      replaced_by_term_id: replacedByTermId.value || undefined
    })
    uiStore.showSuccess('Term Deprecated', `"${props.term.label}" has been deprecated`)
    emit('deprecated')
    emit('update:visible', false)
  } catch (e) {
    uiStore.showError('Deprecation Failed', (e as Error).message)
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
    header="Deprecate Term"
    :style="{ width: '450px' }"
    modal
  >
    <div class="deprecate-form">
      <div class="term-info">
        <p>You are about to deprecate:</p>
        <div class="term-badge">
          <strong>{{ term.label }}</strong>
          <span class="term-code">({{ term.value }})</span>
        </div>
      </div>

      <div class="form-field">
        <label for="reason">Reason for deprecation *</label>
        <Textarea
          id="reason"
          v-model="reason"
          rows="3"
          placeholder="Why is this term being deprecated?"
          :class="{ 'p-invalid': error }"
        />
        <small v-if="error" class="p-error">{{ error }}</small>
      </div>

      <div class="form-field">
        <label for="replacedBy">Replacement term (optional)</label>
        <Dropdown
          id="replacedBy"
          v-model="replacedByTermId"
          :options="replacementOptions"
          option-label="label"
          option-value="value"
          placeholder="Select a replacement..."
          show-clear
          class="w-full"
        />
        <small class="help-text">
          If there's an alternative term, users will be guided to use it instead
        </small>
      </div>
    </div>

    <template #footer>
      <Button
        label="Cancel"
        severity="secondary"
        text
        @click="cancel"
        :disabled="loading"
      />
      <Button
        label="Deprecate"
        severity="warn"
        @click="submit"
        :loading="loading"
      />
    </template>
  </Dialog>
</template>

<style scoped>
.deprecate-form {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}

.term-info {
  background: var(--p-surface-100);
  padding: 1rem;
  border-radius: 6px;
}

.term-info p {
  margin: 0 0 0.5rem 0;
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
}

.term-badge {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.term-code {
  font-family: monospace;
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
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

.form-field textarea {
  width: 100%;
}

.help-text {
  color: var(--p-text-muted-color);
  font-size: 0.75rem;
}

.w-full {
  width: 100%;
}
</style>
