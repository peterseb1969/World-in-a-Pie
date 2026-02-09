<script setup lang="ts">
import { ref, onMounted, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import Breadcrumb from 'primevue/breadcrumb'
import Card from 'primevue/card'
import Tag from 'primevue/tag'
import Button from 'primevue/button'
import TabView from 'primevue/tabview'
import TabPanel from 'primevue/tabpanel'
import Skeleton from 'primevue/skeleton'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import { useConfirm } from 'primevue/useconfirm'
import { useTerminologyStore, useTemplateStore, useUiStore } from '@/stores'
import { defStoreClient } from '@/api/client'
import TermList from '@/components/terminologies/TermList.vue'
import TerminologyForm from '@/components/terminologies/TerminologyForm.vue'
import BulkTermImport from '@/components/terminologies/BulkTermImport.vue'
import type { Template } from '@/types'

const props = defineProps<{
  id: string
}>()

const router = useRouter()
const confirm = useConfirm()
const terminologyStore = useTerminologyStore()
const templateStore = useTemplateStore()
const uiStore = useUiStore()

const showEditDialog = ref(false)
const showBulkImport = ref(false)
const exporting = ref(false)
const loadingUsage = ref(false)

const breadcrumbItems = computed(() => [
  { label: 'Terminologies', command: () => router.push('/terminologies') },
  { label: terminologyStore.currentTerminology?.name || 'Loading...' }
])

const breadcrumbHome = { icon: 'pi pi-home', command: () => router.push('/') }

// Computed: templates that use this terminology
const templatesUsingTerminology = computed(() => {
  if (!terminologyStore.currentTerminology) return []
  const termId = terminologyStore.currentTerminology.terminology_id
  const termCode = terminologyStore.currentTerminology.code

  return templateStore.templates.filter(template => {
    return template.fields.some(field => {
      // Check direct terminology reference
      if (field.terminology_ref === termId || field.terminology_ref === termCode) {
        return true
      }
      // Check array terminology reference
      if (field.array_terminology_ref === termId || field.array_terminology_ref === termCode) {
        return true
      }
      return false
    })
  })
})

onMounted(async () => {
  await loadTerminology()
  await loadTemplateUsage()
})

async function loadTemplateUsage() {
  loadingUsage.value = true
  try {
    await templateStore.fetchTemplates({ page_size: 100 })
  } catch (e) {
    console.warn('Failed to load template usage:', e)
  } finally {
    loadingUsage.value = false
  }
}

watch(
  () => props.id,
  async () => {
    await loadTerminology()
  }
)

async function loadTerminology() {
  try {
    await terminologyStore.fetchTerminology(props.id)
  } catch (e) {
    uiStore.showError('Failed to load terminology', (e as Error).message)
    router.push('/terminologies')
  }
}

function getStatusSeverity(status: string): 'success' | 'warn' | 'danger' | 'info' | 'secondary' | 'contrast' | undefined {
  switch (status) {
    case 'active': return 'info'
    case 'deprecated': return 'warn'
    case 'inactive': return 'danger'
    default: return 'secondary'
  }
}

function editTerminology() {
  showEditDialog.value = true
}

async function onUpdated() {
  showEditDialog.value = false
  await loadTerminology()
}

function confirmDeactivate() {
  if (!terminologyStore.currentTerminology) return

  confirm.require({
    message: `Are you sure you want to deactivate "${terminologyStore.currentTerminology.name}"? This will also deactivate all terms. It can be restored later.`,
    header: 'Deactivate Terminology',
    icon: 'pi pi-exclamation-triangle',
    rejectClass: 'p-button-secondary p-button-text',
    acceptClass: 'p-button-danger',
    accept: async () => {
      try {
        await terminologyStore.deleteTerminology(props.id)
        uiStore.showSuccess('Terminology Deactivated')
        router.push('/terminologies')
      } catch (e) {
        uiStore.showError('Deactivation Failed', (e as Error).message)
      }
    }
  })
}

async function exportTerminology(format: 'json' | 'csv') {
  exporting.value = true
  try {
    const data = await defStoreClient.exportTerminology(props.id, format)

    let blob: Blob
    let filename: string

    if (format === 'json') {
      blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
      filename = `${terminologyStore.currentTerminology?.code || 'terminology'}.json`
    } else {
      blob = new Blob([data as string], { type: 'text/csv' })
      filename = `${terminologyStore.currentTerminology?.code || 'terminology'}.csv`
    }

    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    link.click()
    URL.revokeObjectURL(url)

    uiStore.showSuccess('Export Complete', `Downloaded ${filename}`)
  } catch (e) {
    uiStore.showError('Export Failed', (e as Error).message)
  } finally {
    exporting.value = false
  }
}

function formatDate(dateStr: string) {
  return new Date(dateStr).toLocaleString()
}

function navigateToTemplate(template: Template) {
  router.push(`/templates/${template.template_id}`)
}

function getFieldsUsingTerminology(template: Template): string[] {
  if (!terminologyStore.currentTerminology) return []
  const termId = terminologyStore.currentTerminology.terminology_id
  const termCode = terminologyStore.currentTerminology.code

  return template.fields
    .filter(field =>
      field.terminology_ref === termId ||
      field.terminology_ref === termCode ||
      field.array_terminology_ref === termId ||
      field.array_terminology_ref === termCode
    )
    .map(field => field.name)
}
</script>

<template>
  <div class="terminology-detail-view">
    <Breadcrumb :model="breadcrumbItems" :home="breadcrumbHome" class="breadcrumb" />

    <div v-if="terminologyStore.loading && !terminologyStore.currentTerminology" class="loading-state">
      <Skeleton width="200px" height="2rem" class="mb-2" />
      <Skeleton width="100%" height="150px" />
    </div>

    <template v-else-if="terminologyStore.currentTerminology">
      <div class="header-section">
        <div class="header-info">
          <div class="title-row">
            <h1>{{ terminologyStore.currentTerminology.name }}</h1>
            <Tag
              :value="terminologyStore.currentTerminology.status"
              :severity="getStatusSeverity(terminologyStore.currentTerminology.status)"
            />
          </div>
          <div class="code-row">
            <span class="code-badge">{{ terminologyStore.currentTerminology.code }}</span>
            <span class="id-badge">{{ terminologyStore.currentTerminology.terminology_id }}</span>
          </div>
          <p v-if="terminologyStore.currentTerminology.description" class="description">
            {{ terminologyStore.currentTerminology.description }}
          </p>
        </div>

        <div class="header-actions">
          <Button
            label="Edit"
            icon="pi pi-pencil"
            severity="secondary"
            @click="editTerminology"
          />
          <Button
            icon="pi pi-download"
            severity="secondary"
            title="Export JSON"
            :loading="exporting"
            @click="exportTerminology('json')"
          />
          <Button
            icon="pi pi-ban"
            severity="danger"
            title="Deactivate"
            @click="confirmDeactivate"
          />
        </div>
      </div>

      <Card class="info-card">
        <template #content>
          <div class="info-grid">
            <div class="info-item">
              <span class="info-label">Terms</span>
              <span class="info-value">{{ terminologyStore.currentTerminology.term_count }}</span>
            </div>
            <div class="info-item">
              <span class="info-label">Language</span>
              <span class="info-value">{{ terminologyStore.currentTerminology.metadata?.language || 'en' }}</span>
            </div>
            <div class="info-item">
              <span class="info-label">Created</span>
              <span class="info-value">{{ formatDate(terminologyStore.currentTerminology.created_at) }}</span>
            </div>
          </div>
        </template>
      </Card>

      <TabView>
        <TabPanel value="0" header="Terms">
          <div class="terms-section">
            <div class="terms-header">
              <Button
                label="Bulk Import"
                icon="pi pi-upload"
                severity="secondary"
                @click="showBulkImport = true"
              />
            </div>
            <TermList :terminology-id="id" />
          </div>
        </TabPanel>

        <TabPanel value="1" header="Metadata">
          <div class="metadata-section">
            <div v-if="terminologyStore.currentTerminology.metadata" class="metadata-grid">
              <div v-if="terminologyStore.currentTerminology.metadata.source" class="metadata-item">
                <span class="metadata-label">Source</span>
                <span class="metadata-value">{{ terminologyStore.currentTerminology.metadata.source }}</span>
              </div>
              <div v-if="terminologyStore.currentTerminology.metadata.source_url" class="metadata-item">
                <span class="metadata-label">Source URL</span>
                <a :href="terminologyStore.currentTerminology.metadata.source_url" target="_blank" class="metadata-value link">
                  {{ terminologyStore.currentTerminology.metadata.source_url }}
                </a>
              </div>
              <div v-if="terminologyStore.currentTerminology.metadata.version" class="metadata-item">
                <span class="metadata-label">Version</span>
                <span class="metadata-value">{{ terminologyStore.currentTerminology.metadata.version }}</span>
              </div>
            </div>
            <p v-else class="empty-metadata">No additional metadata</p>
          </div>
        </TabPanel>

        <TabPanel value="2">
          <template #header>
            <span class="tab-header">
              <i class="pi pi-file"></i>
              Usage
              <Tag v-if="templatesUsingTerminology.length > 0" :value="templatesUsingTerminology.length.toString()" severity="info" rounded />
            </span>
          </template>
          <div class="usage-section">
            <p class="usage-description">
              Templates that use this terminology for term fields.
            </p>
            <DataTable
              :value="templatesUsingTerminology"
              :loading="loadingUsage"
              size="small"
              @row-click="(e) => navigateToTemplate(e.data)"
              class="usage-table"
              :pt="{ bodyRow: { style: 'cursor: pointer' } }"
            >
              <Column field="name" header="Template" style="min-width: 200px">
                <template #body="{ data }">
                  <div class="template-name">
                    <span class="name">{{ data.name }}</span>
                    <code class="code">{{ data.code }}</code>
                  </div>
                </template>
              </Column>
              <Column header="Fields Using" style="min-width: 200px">
                <template #body="{ data }">
                  <div class="field-badges">
                    <Tag
                      v-for="fieldName in getFieldsUsingTerminology(data)"
                      :key="fieldName"
                      :value="fieldName"
                      severity="secondary"
                    />
                  </div>
                </template>
              </Column>
              <Column field="status" header="Status" style="width: 100px">
                <template #body="{ data }">
                  <Tag :value="data.status" :severity="getStatusSeverity(data.status)" />
                </template>
              </Column>
              <Column header="" style="width: 60px">
                <template #body>
                  <Button
                    icon="pi pi-arrow-right"
                    severity="secondary"
                    text
                    rounded
                    size="small"
                  />
                </template>
              </Column>
              <template #empty>
                <div class="empty-usage">
                  <i class="pi pi-info-circle"></i>
                  <p>No templates are using this terminology</p>
                </div>
              </template>
            </DataTable>
          </div>
        </TabPanel>
      </TabView>
    </template>

    <TerminologyForm
      v-if="terminologyStore.currentTerminology"
      v-model:visible="showEditDialog"
      :terminology="terminologyStore.currentTerminology"
      @updated="onUpdated"
    />

    <BulkTermImport
      v-model:visible="showBulkImport"
      :terminology-id="id"
    />
  </div>
</template>

<style scoped>
.terminology-detail-view {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.breadcrumb {
  background: transparent;
  padding: 0;
}

.loading-state {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.header-section {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 1rem;
  flex-wrap: wrap;
}

.header-info {
  flex: 1;
}

.title-row {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.title-row h1 {
  margin: 0;
  font-size: 1.75rem;
}

.code-row {
  display: flex;
  gap: 0.75rem;
  margin-top: 0.5rem;
}

.code-badge {
  font-family: monospace;
  background: var(--p-surface-100);
  padding: 0.25rem 0.5rem;
  border-radius: 4px;
  font-size: 0.875rem;
}

.id-badge {
  font-family: monospace;
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
}

.description {
  margin: 0.75rem 0 0 0;
  color: var(--p-text-muted-color);
}

.header-actions {
  display: flex;
  gap: 0.5rem;
}

.info-card {
  background: var(--p-surface-card);
}

.info-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 1.5rem;
}

.info-item {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.info-label {
  font-size: 0.75rem;
  text-transform: uppercase;
  color: var(--p-text-muted-color);
  letter-spacing: 0.05em;
}

.info-value {
  font-weight: 500;
}

.info-value i.pi-check {
  color: var(--p-green-500);
}

.info-value i.pi-times {
  color: var(--p-text-muted-color);
}

.terms-section {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.terms-header {
  display: flex;
  justify-content: flex-end;
}

.metadata-section {
  padding: 1rem 0;
}

.metadata-grid {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.metadata-item {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.metadata-label {
  font-size: 0.75rem;
  text-transform: uppercase;
  color: var(--p-text-muted-color);
  letter-spacing: 0.05em;
}

.metadata-value {
  font-weight: 500;
}

.metadata-value.link {
  color: var(--p-primary-color);
  text-decoration: none;
}

.metadata-value.link:hover {
  text-decoration: underline;
}

.empty-metadata {
  color: var(--p-text-muted-color);
  font-style: italic;
}

.mb-2 {
  margin-bottom: 0.5rem;
}

/* Usage tab styles */
.tab-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.usage-section {
  padding: 1rem 0;
}

.usage-description {
  margin: 0 0 1rem 0;
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
}

.usage-table :deep(.p-datatable-tbody > tr:hover) {
  background-color: var(--p-surface-100);
}

.template-name {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.template-name .name {
  font-weight: 500;
}

.template-name .code {
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
  background: var(--p-surface-100);
  padding: 0.125rem 0.25rem;
  border-radius: 3px;
  width: fit-content;
}

.field-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
}

.empty-usage {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.75rem;
  padding: 2rem;
  color: var(--p-text-muted-color);
}

.empty-usage i {
  font-size: 1.5rem;
  opacity: 0.5;
}
</style>
