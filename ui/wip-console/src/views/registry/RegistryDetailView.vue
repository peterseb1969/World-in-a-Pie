<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Card from 'primevue/card'
import Tag from 'primevue/tag'
import Button from 'primevue/button'
import Breadcrumb from 'primevue/breadcrumb'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import ConfirmDialog from 'primevue/confirmdialog'
import { useConfirm } from 'primevue/useconfirm'
import { registryClient } from '@/api/client'
import { useUiStore } from '@/stores'
import type { RegistryEntryFull, RegistrySynonym } from '@/types'
import CompositeKeyDisplay from '@/components/registry/CompositeKeyDisplay.vue'
import EntityLink from '@/components/registry/EntityLink.vue'
import SynonymTable from '@/components/registry/SynonymTable.vue'
import AddSynonymDialog from '@/components/registry/AddSynonymDialog.vue'
import MergeDialog from '@/components/registry/MergeDialog.vue'

const route = useRoute()
const router = useRouter()
const uiStore = useUiStore()
const confirm = useConfirm()

const entryId = computed(() => route.params.id as string)
const entry = ref<RegistryEntryFull | null>(null)
const loading = ref(false)

// Dialog states
const showAddSynonym = ref(false)
const showMergeDialog = ref(false)

const breadcrumbItems = computed(() => [
  { label: 'Registry', command: () => router.push({ name: 'registry' }) },
  { label: entryId.value },
])
const breadcrumbHome = { icon: 'pi pi-home', command: () => router.push('/') }

onMounted(() => {
  loadEntry()
})

async function loadEntry() {
  loading.value = true
  try {
    entry.value = await registryClient.getEntry(entryId.value)
  } catch (e) {
    uiStore.showError('Failed to load entry', (e as Error).message)
  } finally {
    loading.value = false
  }
}

function getStatusSeverity(status: string): 'success' | 'warn' | 'danger' | 'secondary' {
  switch (status) {
    case 'active': return 'success'
    case 'reserved': return 'warn'
    case 'inactive': return 'danger'
    default: return 'secondary'
  }
}

function getEntityTypeIcon(type: string): string {
  switch (type) {
    case 'terminologies': return 'pi pi-book'
    case 'terms': return 'pi pi-tag'
    case 'templates': return 'pi pi-file'
    case 'documents': return 'pi pi-folder'
    case 'files': return 'pi pi-images'
    default: return 'pi pi-circle'
  }
}

function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleString()
}

async function handleRemoveSynonym(synonym: RegistrySynonym) {
  try {
    const result = await registryClient.removeSynonym({
      target_id: entryId.value,
      synonym_namespace: synonym.namespace,
      synonym_entity_type: synonym.entity_type,
      synonym_composite_key: synonym.composite_key,
    })
    if (result.status === 'removed') {
      uiStore.showSuccess('Synonym removed')
      await loadEntry()
    } else {
      uiStore.showError('Failed to remove synonym', result.error || result.status)
    }
  } catch (e) {
    uiStore.showError('Failed to remove synonym', (e as Error).message)
  }
}

function confirmDeactivate() {
  confirm.require({
    message: `Deactivate entry ${entryId.value}? This will make it unresolvable.`,
    header: 'Confirm Deactivation',
    icon: 'pi pi-exclamation-triangle',
    acceptClass: 'p-button-danger',
    accept: executeDeactivate,
  })
}

async function executeDeactivate() {
  try {
    await registryClient.deactivateEntry(entryId.value)
    uiStore.showSuccess('Entry deactivated')
    await loadEntry()
  } catch (e) {
    uiStore.showError('Failed to deactivate', (e as Error).message)
  }
}
</script>

<template>
  <div class="registry-detail-view">
    <ConfirmDialog />

    <!-- Breadcrumb -->
    <Breadcrumb :home="breadcrumbHome" :model="breadcrumbItems" class="breadcrumb" />

    <!-- Loading -->
    <div v-if="loading" class="loading-state">
      <i class="pi pi-spin pi-spinner"></i>
      Loading entry...
    </div>

    <template v-else-if="entry">
      <!-- Header -->
      <div class="detail-header">
        <div class="header-main">
          <code class="entry-id-large">{{ entry.entry_id }}</code>
          <Tag :value="entry.status" :severity="getStatusSeverity(entry.status)" />
          <span class="entity-type-badge">
            <i :class="getEntityTypeIcon(entry.entity_type)"></i>
            {{ entry.entity_type }}
          </span>
          <code class="namespace-badge">{{ entry.namespace }}</code>
        </div>
        <div class="header-actions">
          <Button
            icon="pi pi-plus"
            label="Add Synonym"
            outlined
            size="small"
            @click="showAddSynonym = true"
          />
          <Button
            icon="pi pi-share-alt"
            label="Merge with..."
            outlined
            size="small"
            :disabled="entry.status !== 'active'"
            @click="showMergeDialog = true"
          />
          <Button
            icon="pi pi-ban"
            label="Deactivate"
            severity="danger"
            outlined
            size="small"
            :disabled="entry.status === 'inactive'"
            @click="confirmDeactivate"
          />
          <EntityLink :entityType="entry.entity_type" :entryId="entry.entry_id" />
        </div>
      </div>

      <!-- Identity Card -->
      <Card class="section-card">
        <template #title>Identity</template>
        <template #content>
          <div class="detail-grid">
            <span class="detail-label">Entry ID</span>
            <code>{{ entry.entry_id }}</code>

            <span class="detail-label">Namespace</span>
            <code class="namespace-badge">{{ entry.namespace }}</code>

            <span class="detail-label">Entity Type</span>
            <span class="entity-type-badge">
              <i :class="getEntityTypeIcon(entry.entity_type)"></i>
              {{ entry.entity_type }}
            </span>

            <span class="detail-label">Is Preferred</span>
            <Tag :value="entry.is_preferred ? 'Yes' : 'No'" :severity="entry.is_preferred ? 'success' : 'warn'" />

            <span class="detail-label">Status</span>
            <Tag :value="entry.status" :severity="getStatusSeverity(entry.status)" />

            <span class="detail-label">Created</span>
            <span>{{ formatDate(entry.created_at) }} <span v-if="entry.created_by" class="by-text">by {{ entry.created_by }}</span></span>

            <span class="detail-label">Updated</span>
            <span>{{ formatDate(entry.updated_at) }} <span v-if="entry.updated_by" class="by-text">by {{ entry.updated_by }}</span></span>
          </div>
        </template>
      </Card>

      <!-- Primary Composite Key -->
      <Card class="section-card">
        <template #title>Primary Composite Key</template>
        <template #content>
          <CompositeKeyDisplay :compositeKey="entry.primary_composite_key" />
          <div class="hash-display">
            <span class="detail-label">Hash</span>
            <code class="hash-value">{{ entry.primary_composite_key_hash || '(none)' }}</code>
          </div>
        </template>
      </Card>

      <!-- Synonyms -->
      <Card class="section-card">
        <template #title>Synonyms ({{ entry.synonyms.length }})</template>
        <template #content>
          <SynonymTable
            :synonyms="entry.synonyms"
            :entryId="entry.entry_id"
            @remove="handleRemoveSynonym"
            @add="showAddSynonym = true"
          />
        </template>
      </Card>

      <!-- Additional IDs -->
      <Card v-if="entry.additional_ids.length > 0" class="section-card">
        <template #title>Additional IDs ({{ entry.additional_ids.length }})</template>
        <template #content>
          <DataTable :value="entry.additional_ids" size="small" stripedRows>
            <Column header="ID" style="width: 200px">
              <template #body="{ data }">
                <code>{{ data.id }}</code>
              </template>
            </Column>
            <Column header="Namespace">
              <template #body="{ data }">
                <code class="namespace-badge">{{ data.namespace }}</code>
              </template>
            </Column>
            <Column header="Entity Type">
              <template #body="{ data }">
                {{ data.entity_type }}
              </template>
            </Column>
          </DataTable>
        </template>
      </Card>

      <!-- Source Info -->
      <Card v-if="entry.source_info" class="section-card">
        <template #title>Source Info</template>
        <template #content>
          <div class="detail-grid">
            <span class="detail-label">System ID</span>
            <span>{{ entry.source_info.system_id }}</span>
            <span class="detail-label">Endpoint</span>
            <code v-if="entry.source_info.endpoint_url">{{ entry.source_info.endpoint_url }}</code>
            <span v-else class="text-muted">N/A</span>
          </div>
        </template>
      </Card>

      <!-- Metadata -->
      <Card v-if="entry.metadata && Object.keys(entry.metadata).length > 0" class="section-card">
        <template #title>Metadata</template>
        <template #content>
          <pre class="metadata-json">{{ JSON.stringify(entry.metadata, null, 2) }}</pre>
        </template>
      </Card>
    </template>

    <!-- Not Found -->
    <div v-else class="not-found">
      <i class="pi pi-exclamation-circle" style="font-size: 2rem; opacity: 0.3"></i>
      <p>Entry not found or failed to load.</p>
      <Button label="Back to Registry" icon="pi pi-arrow-left" text @click="router.push({ name: 'registry' })" />
    </div>

    <!-- Dialogs -->
    <AddSynonymDialog
      v-if="entry"
      :visible="showAddSynonym"
      :entryId="entry.entry_id"
      @update:visible="showAddSynonym = $event"
      @added="loadEntry"
    />

    <MergeDialog
      v-if="entry"
      :visible="showMergeDialog"
      :preferredEntry="entry"
      @update:visible="showMergeDialog = $event"
      @merged="loadEntry"
    />
  </div>
</template>

<style scoped>
.registry-detail-view {
  padding: 0;
}

.breadcrumb {
  margin-bottom: 1rem;
  background: transparent;
  padding: 0;
}

.loading-state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  padding: 3rem;
  color: var(--p-text-muted-color);
}

.detail-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 1.5rem;
  flex-wrap: wrap;
  gap: 1rem;
}

.header-main {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  flex-wrap: wrap;
}

.entry-id-large {
  font-size: 1.25rem;
  font-weight: 700;
}

.header-actions {
  display: flex;
  gap: 0.5rem;
  align-items: center;
  flex-wrap: wrap;
}

.section-card {
  margin-bottom: 1rem;
}

.detail-grid {
  display: grid;
  grid-template-columns: 120px 1fr;
  gap: 0.5rem 1rem;
  font-size: 0.875rem;
}

.detail-label {
  color: var(--p-text-muted-color);
  font-weight: 500;
}

.namespace-badge {
  font-size: 0.8rem;
  background: var(--p-surface-100);
  padding: 0.125rem 0.375rem;
  border-radius: 3px;
  display: inline-block;
}

.entity-type-badge {
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
  font-size: 0.875rem;
}

.entity-type-badge i {
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
}

.by-text {
  color: var(--p-text-muted-color);
  font-size: 0.8125rem;
}

.hash-display {
  margin-top: 0.75rem;
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.hash-value {
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
  word-break: break-all;
}

.metadata-json {
  font-size: 0.8125rem;
  background: var(--p-surface-50);
  padding: 0.75rem;
  border-radius: 6px;
  overflow-x: auto;
  margin: 0;
}

.text-muted {
  color: var(--p-text-muted-color);
}

.not-found {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.75rem;
  padding: 3rem;
  color: var(--p-text-muted-color);
}
</style>
