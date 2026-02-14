<script setup lang="ts">
import { computed, ref } from 'vue'
import Button from 'primevue/button'
import type { Template } from '@/types'

const props = defineProps<{
  template: Template | null
  resolved: boolean
}>()

const copied = ref(false)

const jsonPreview = computed(() => {
  if (!props.template) {
    return '{}'
  }

  // Create a clean view of the template
  const preview = {
    template_id: props.template.template_id,
    value: props.template.value,
    label: props.template.label,
    description: props.template.description,
    version: props.template.version,
    extends: props.template.extends,
    status: props.template.status,
    identity_fields: props.template.identity_fields,
    fields: props.template.fields.map(f => ({
      name: f.name,
      label: f.label,
      type: f.type,
      mandatory: f.mandatory,
      ...(f.default_value !== undefined && { default_value: f.default_value }),
      ...(f.terminology_ref && { terminology_ref: f.terminology_ref }),
      ...(f.template_ref && { template_ref: f.template_ref }),
      ...(f.array_item_type && { array_item_type: f.array_item_type }),
      ...(f.array_terminology_ref && { array_terminology_ref: f.array_terminology_ref }),
      ...(f.array_template_ref && { array_template_ref: f.array_template_ref }),
      ...(f.validation && { validation: f.validation })
    })),
    rules: props.template.rules,
    metadata: props.template.metadata
  }

  return JSON.stringify(preview, null, 2)
})

async function copyToClipboard() {
  try {
    await navigator.clipboard.writeText(jsonPreview.value)
    copied.value = true
    setTimeout(() => {
      copied.value = false
    }, 2000)
  } catch (e) {
    console.error('Failed to copy:', e)
  }
}
</script>

<template>
  <div class="template-preview">
    <div class="preview-header">
      <div class="preview-info">
        <span class="view-type">
          {{ resolved ? 'Resolved View' : 'Raw View' }}
        </span>
        <span v-if="resolved && template?.extends" class="inheritance-note">
          (includes inherited fields from parent)
        </span>
      </div>
      <Button
        :label="copied ? 'Copied!' : 'Copy JSON'"
        :icon="copied ? 'pi pi-check' : 'pi pi-copy'"
        severity="secondary"
        size="small"
        @click="copyToClipboard"
      />
    </div>

    <div class="json-container">
      <pre><code>{{ jsonPreview }}</code></pre>
    </div>

    <div v-if="!template" class="empty-state">
      <i class="pi pi-file-edit"></i>
      <p>No template to preview</p>
    </div>
  </div>
</template>

<style scoped>
.template-preview {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.preview-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.preview-info {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.view-type {
  font-weight: 500;
}

.inheritance-note {
  font-size: 0.875rem;
  color: var(--p-text-muted-color);
}

.json-container {
  background-color: var(--p-surface-50);
  border: 1px solid var(--p-surface-border);
  border-radius: var(--p-border-radius);
  overflow: auto;
  max-height: 500px;
}

.json-container pre {
  margin: 0;
  padding: 1rem;
  font-size: 0.875rem;
  line-height: 1.5;
}

.json-container code {
  font-family: 'Menlo', 'Monaco', 'Courier New', monospace;
  color: var(--p-text-color);
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.75rem;
  padding: 2rem;
  color: var(--p-text-muted-color);
}

.empty-state i {
  font-size: 2rem;
}
</style>
