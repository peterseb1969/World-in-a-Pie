<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import Card from 'primevue/card'
import Button from 'primevue/button'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Tag from 'primevue/tag'
import Message from 'primevue/message'
import ProgressSpinner from 'primevue/progressspinner'
import { useAuthStore, useTerminologyStore, useTemplateStore, useUiStore, useNamespaceStore, useIntegrityStore } from '@/stores'
import { getDocumentTitle, hasDocumentTitle } from '@/utils/document'
import { isReportingEnabled, isFilesEnabled } from '@/config/modules'
import { documentStoreClient, fileStoreClient } from '@/api/client'
import type { Terminology, Template, Document } from '@/types'

const router = useRouter()
const authStore = useAuthStore()
const terminologyStore = useTerminologyStore()
const templateStore = useTemplateStore()
const uiStore = useUiStore()
const namespaceStore = useNamespaceStore()
const integrityStore = useIntegrityStore()

// Check if reporting module is enabled
const reportingEnabled = isReportingEnabled()

const loading = ref(true)

// Compact stats for all 5 entity types
const entityCounts = ref<Record<string, number>>({
  terminologies: 0,
  terms: 0,
  templates: 0,
  documents: 0,
  files: 0
})

// Recent items
const recentTerminologies = ref<Terminology[]>([])
const recentTemplates = ref<Template[]>([])
const recentDocuments = ref<Document[]>([])

async function loadDashboard() {
  if (!authStore.isAuthenticated) {
    loading.value = false
    return
  }

  loading.value = true
  try {
    // Fetch terminologies — use API total as authoritative count
    await terminologyStore.fetchTerminologies({ page_size: 100 })
    const allTerms = terminologyStore.terminologies
    entityCounts.value.terminologies = terminologyStore.total
    // Sum term_count across all terminologies for total terms
    entityCounts.value.terms = allTerms.reduce((sum, t) => sum + (t.term_count || 0), 0)
    recentTerminologies.value = [...allTerms]
      .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime())
      .slice(0, 5)

    // Fetch templates — use API total as authoritative count
    await templateStore.fetchTemplates({ page_size: 100 })
    const templates = templateStore.templates
    entityCounts.value.templates = templateStore.total
    recentTemplates.value = [...templates]
      .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime())
      .slice(0, 5)

    // Fetch recent documents — use API total as authoritative count
    try {
      const docParams: Record<string, unknown> = { page_size: 5 }
      if (namespaceStore.currentNamespaceParam) {
        docParams.namespace = namespaceStore.currentNamespaceParam
      }
      const docResponse = await documentStoreClient.listDocuments(docParams as any)
      recentDocuments.value = docResponse.items
      entityCounts.value.documents = docResponse.total
    } catch {
      // Document store may not be available
    }

    // Fetch file count (file storage may not be enabled)
    if (isFilesEnabled()) {
      try {
        const fileParams: Record<string, unknown> = { page_size: 1 }
        if (namespaceStore.currentNamespaceParam) {
          fileParams.namespace = namespaceStore.currentNamespaceParam
        }
        const fileResponse = await fileStoreClient.listFiles(fileParams as any)
        entityCounts.value.files = fileResponse.total
      } catch {
        // File storage may not be available
      }
    }

  } catch (error) {
    uiStore.showError('Failed to load dashboard', error instanceof Error ? error.message : 'Unknown error')
  } finally {
    loading.value = false
  }

  // Integrity check is manual — user clicks Refresh in the Data Quality card
}

function loadIntegrityCheck() {
  if (!authStore.isAuthenticated) return
  integrityStore.run({ document_limit: 5000, check_term_refs: true, recent_first: true })
}

function getStatusSeverity(status: string): 'success' | 'warn' | 'danger' | 'secondary' {
  switch (status) {
    case 'active': return 'success'
    case 'inactive': return 'danger'
    default: return 'secondary'
  }
}

function navigateToTerminology(terminology: Terminology) {
  router.push(`/terminologies/${terminology.terminology_id}`)
}

function navigateToTemplate(template: Template) {
  router.push(`/templates/${template.template_id}`)
}

function navigateToDocument(doc: Document) {
  router.push(`/documents/${doc.document_id}`)
}

function formatTimeAgo(timestamp: string): string {
  const diffMs = Date.now() - new Date(timestamp).getTime()
  const diffMins = Math.floor(diffMs / 60000)
  if (diffMins < 1) return 'just now'
  if (diffMins < 60) return `${diffMins}m ago`
  const diffHours = Math.floor(diffMs / 3600000)
  if (diffHours < 24) return `${diffHours}h ago`
  return `${Math.floor(diffMs / 86400000)}d ago`
}

function getIntegritySeverity(status: string): 'success' | 'warn' | 'danger' | 'secondary' {
  switch (status) {
    case 'healthy': return 'success'
    case 'warning': return 'warn'
    case 'error': return 'danger'
    case 'partial': return 'secondary'
    default: return 'secondary'
  }
}

const statItems = [
  { key: 'terminologies', label: 'Terminologies', icon: 'pi pi-book', route: '/terminologies' },
  { key: 'terms', label: 'Terms', icon: 'pi pi-tag', route: '/terminologies' },
  { key: 'templates', label: 'Templates', icon: 'pi pi-file', route: '/templates' },
  { key: 'documents', label: 'Documents', icon: 'pi pi-folder', route: '/documents' },
  { key: 'files', label: 'Files', icon: 'pi pi-paperclip', route: '/files' }
]

onMounted(() => {
  loadDashboard()
})

// Watch for auth changes and reload dashboard when user logs in
watch(
  () => authStore.isAuthenticated,
  (isAuth, wasAuth) => {
    if (isAuth && !wasAuth) {
      loadDashboard()
    }
  }
)

// Watch for namespace changes and reload dashboard
watch(() => namespaceStore.current, () => {
  loadDashboard()
})
</script>

<template>
  <div class="home-view">
    <div class="page-header">
      <h1>Dashboard</h1>
      <p class="subtitle">Namespace: <strong>{{ namespaceStore.isAll ? 'All' : namespaceStore.current }}</strong></p>
    </div>

    <!-- Auth warning -->
    <div v-if="!authStore.isAuthenticated" class="auth-warning">
      <Card>
        <template #content>
          <div class="warning-content">
            <i class="pi pi-exclamation-triangle"></i>
            <div>
              <h3>API Key Required</h3>
              <p>Please configure your API key to access WIP services. Click the key icon in the sidebar footer.</p>
            </div>
          </div>
        </template>
      </Card>
    </div>

    <template v-else>
      <!-- Compact stat bar -->
      <div class="stat-bar">
        <div
          v-for="item in statItems"
          :key="item.key"
          class="stat-chip"
          @click="router.push(item.route)"
        >
          <i :class="item.icon"></i>
          <span class="stat-chip-count">{{ entityCounts[item.key] }}</span>
          <span class="stat-chip-label">{{ item.label }}</span>
        </div>
      </div>

      <!-- Quick Actions Row (write+ permission required) -->
      <div v-if="namespaceStore.canWrite" class="quick-actions-row">
        <Button
          label="New Terminology"
          icon="pi pi-plus"
          severity="secondary"
          size="small"
          @click="router.push('/terminologies?create=true')"
        />
        <Button
          label="Import Terminology"
          icon="pi pi-upload"
          severity="secondary"
          size="small"
          @click="router.push('/terminologies/import')"
        />
        <Button
          label="New Template"
          icon="pi pi-plus"
          severity="secondary"
          size="small"
          @click="router.push('/templates/new')"
        />
        <Button
          label="New Document"
          icon="pi pi-plus"
          severity="secondary"
          size="small"
          @click="router.push('/documents/new')"
        />
        <Button
          label="Upload File"
          icon="pi pi-cloud-upload"
          severity="secondary"
          size="small"
          @click="router.push('/files/upload')"
        />
        <Button
          label="Validate Values"
          icon="pi pi-check-circle"
          severity="secondary"
          size="small"
          @click="router.push('/terminologies/validate')"
        />
      </div>

      <!-- Data Quality Section (only shown when reporting module is enabled) -->
      <div v-if="reportingEnabled" class="data-quality-section">
        <Card class="quality-card">
          <template #title>
            <div class="card-title">
              <i class="pi pi-shield"></i>
              <span>Data Quality</span>
              <template v-if="integrityStore.result">
                <Tag
                  :severity="getIntegritySeverity(integrityStore.result.status)"
                  class="status-tag"
                >
                  {{ integrityStore.result.status }}
                </Tag>
                <span class="checked-at" :title="new Date(integrityStore.result.checked_at).toLocaleString()">
                  {{ formatTimeAgo(integrityStore.result.checked_at) }}
                </span>
              </template>
            </div>
          </template>
          <template #content>
            <div v-if="integrityStore.loading" class="quality-loading">
              <ProgressSpinner style="width: 30px; height: 30px" />
              <span>Checking data integrity...</span>
            </div>

            <Message v-else-if="integrityStore.error" severity="warn" :closable="false">
              Could not check data integrity: {{ integrityStore.error }}
            </Message>

            <div v-else-if="!integrityStore.result" class="quality-prompt">
              <i class="pi pi-info-circle"></i>
              <span>Click <strong>Refresh</strong> to run an integrity check.</span>
            </div>

            <div v-else class="quality-content">
              <div class="quality-summary">
                <div class="quality-stat">
                  <span class="quality-label">Templates Checked</span>
                  <span class="quality-value">{{ integrityStore.result.summary.total_templates }}</span>
                </div>
                <div class="quality-stat">
                  <span class="quality-label">Documents Checked</span>
                  <span class="quality-value">
                    {{ integrityStore.result.summary.documents_checked ?? integrityStore.result.summary.total_documents }}
                    <span v-if="integrityStore.result.summary.documents_checked && integrityStore.result.summary.documents_checked < integrityStore.result.summary.total_documents" class="quality-of-total">
                      / {{ integrityStore.result.summary.total_documents }}
                    </span>
                  </span>
                </div>
                <div class="quality-stat" :class="{ 'has-issues': integrityStore.result.summary.templates_with_issues > 0 }">
                  <span class="quality-label">Templates with Issues</span>
                  <span class="quality-value">{{ integrityStore.result.summary.templates_with_issues }}</span>
                </div>
                <div class="quality-stat" :class="{ 'has-issues': integrityStore.result.summary.documents_with_issues > 0 }">
                  <span class="quality-label">Documents with Issues</span>
                  <span class="quality-value">{{ integrityStore.result.summary.documents_with_issues }}</span>
                </div>
              </div>

              <div v-if="integrityStore.result.issues.length > 0" class="quality-issues-summary">
                <Message severity="warn" :closable="false">
                  <div class="issues-message">
                    <span>{{ integrityStore.result.issues.length }} issue{{ integrityStore.result.issues.length !== 1 ? 's' : '' }} found</span>
                    <Button
                      label="View Details"
                      icon="pi pi-arrow-right"
                      iconPos="right"
                      text
                      size="small"
                      @click="router.push('/audit')"
                    />
                  </div>
                </Message>
              </div>

              <div v-else class="quality-healthy">
                <i class="pi pi-check-circle"></i>
                <span>All references are valid. No integrity issues found.</span>
              </div>

              <div v-if="integrityStore.result.services_unavailable.length > 0" class="services-warning">
                <Message severity="warn" :closable="false">
                  Some services were unavailable: {{ integrityStore.result.services_unavailable.join(', ') }}
                </Message>
              </div>
            </div>
          </template>
          <template #footer>
            <Button
              label="Refresh"
              icon="pi pi-refresh"
              text
              size="small"
              :loading="integrityStore.loading"
              @click="loadIntegrityCheck"
            />
          </template>
        </Card>
      </div>

      <!-- Recent Items — 3-column grid -->
      <div class="recent-grid">
        <!-- Recent Terminologies -->
        <Card class="recent-card">
          <template #title>
            <div class="card-title">
              <i class="pi pi-book"></i>
              <span>Recent Terminologies</span>
            </div>
          </template>
          <template #content>
            <DataTable
              :value="recentTerminologies"
              :loading="loading"
              size="small"
              @row-click="(e) => navigateToTerminology(e.data)"
              class="clickable-rows"
              :pt="{ bodyRow: { style: 'cursor: pointer' } }"
            >
              <Column field="name" header="Name" />
              <Column field="term_count" header="Terms" style="width: 60px">
                <template #body="{ data }">
                  <Tag severity="info">{{ data.term_count }}</Tag>
                </template>
              </Column>
              <Column field="status" header="Status" style="width: 80px">
                <template #body="{ data }">
                  <Tag :severity="getStatusSeverity(data.status)">{{ data.status }}</Tag>
                </template>
              </Column>
              <template #empty>
                <div class="empty-state">No terminologies</div>
              </template>
            </DataTable>
          </template>
          <template #footer>
            <Button label="All Terminologies" icon="pi pi-arrow-right" iconPos="right" text size="small" @click="router.push('/terminologies')" />
          </template>
        </Card>

        <!-- Recent Templates -->
        <Card class="recent-card">
          <template #title>
            <div class="card-title">
              <i class="pi pi-file"></i>
              <span>Recent Templates</span>
            </div>
          </template>
          <template #content>
            <DataTable
              :value="recentTemplates"
              :loading="loading"
              size="small"
              @row-click="(e) => navigateToTemplate(e.data)"
              class="clickable-rows"
              :pt="{ bodyRow: { style: 'cursor: pointer' } }"
            >
              <Column field="name" header="Name" />
              <Column header="Fields" style="width: 60px">
                <template #body="{ data }">
                  <Tag severity="info">{{ data.fields?.length || 0 }}</Tag>
                </template>
              </Column>
              <Column field="status" header="Status" style="width: 80px">
                <template #body="{ data }">
                  <Tag :severity="getStatusSeverity(data.status)">{{ data.status }}</Tag>
                </template>
              </Column>
              <template #empty>
                <div class="empty-state">No templates</div>
              </template>
            </DataTable>
          </template>
          <template #footer>
            <Button label="All Templates" icon="pi pi-arrow-right" iconPos="right" text size="small" @click="router.push('/templates')" />
          </template>
        </Card>

        <!-- Recent Documents -->
        <Card class="recent-card">
          <template #title>
            <div class="card-title">
              <i class="pi pi-folder"></i>
              <span>Recent Documents</span>
            </div>
          </template>
          <template #content>
            <DataTable
              :value="recentDocuments"
              :loading="loading"
              size="small"
              @row-click="(e) => navigateToDocument(e.data)"
              class="clickable-rows"
              :pt="{ bodyRow: { style: 'cursor: pointer' } }"
            >
              <Column header="Title">
                <template #body="{ data }">
                  <span v-if="hasDocumentTitle(data)" class="doc-title">{{ getDocumentTitle(data) }}</span>
                  <code v-else class="doc-id">{{ data.document_id?.slice(0, 8) }}...</code>
                </template>
              </Column>
              <Column field="template_id" header="Template">
                <template #body="{ data }">
                  {{ data.template_id }}
                </template>
              </Column>
              <Column field="status" header="Status" style="width: 80px">
                <template #body="{ data }">
                  <Tag :severity="getStatusSeverity(data.status)">{{ data.status }}</Tag>
                </template>
              </Column>
              <template #empty>
                <div class="empty-state">No documents</div>
              </template>
            </DataTable>
          </template>
          <template #footer>
            <Button label="All Documents" icon="pi pi-arrow-right" iconPos="right" text size="small" @click="router.push('/documents')" />
          </template>
        </Card>
      </div>
    </template>
  </div>
</template>

<style scoped>
.home-view {
  max-width: 1400px;
  margin: 0 auto;
}

.page-header {
  margin-bottom: 1.5rem;
}

.page-header h1 {
  font-size: 1.75rem;
  font-weight: 600;
  margin-bottom: 0.25rem;
}

.subtitle {
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
}

/* Auth warning */
.auth-warning {
  margin-bottom: 2rem;
}

.warning-content {
  display: flex;
  align-items: flex-start;
  gap: 1rem;
}

.warning-content i {
  font-size: 2rem;
  color: var(--p-orange-500);
}

.warning-content h3 {
  margin: 0 0 0.25rem 0;
  font-size: 1.125rem;
}

.warning-content p {
  margin: 0;
  color: var(--p-text-muted-color);
}

/* Compact stat bar */
.stat-bar {
  display: flex;
  gap: 0.75rem;
  margin-bottom: 1rem;
  flex-wrap: wrap;
}

.stat-chip {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.625rem 1rem;
  background: var(--p-surface-0);
  border: 1px solid var(--p-surface-200);
  border-radius: 8px;
  cursor: pointer;
  transition: border-color 0.2s, box-shadow 0.2s;
  flex: 1;
  min-width: 140px;
}

.stat-chip:hover {
  border-color: var(--p-primary-color);
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.08);
}

.stat-chip i {
  color: var(--p-primary-color);
  font-size: 1rem;
}

.stat-chip-count {
  font-size: 1.25rem;
  font-weight: 700;
  color: var(--p-text-color);
}

.stat-chip-label {
  font-size: 0.8125rem;
  color: var(--p-text-muted-color);
}

/* Quick actions row */
.quick-actions-row {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 1.5rem;
  flex-wrap: wrap;
}

/* Data quality */
.data-quality-section {
  margin-bottom: 1.5rem;
}

.card-title {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.card-title i {
  color: var(--p-primary-color);
}

.quality-card :deep(.p-card-title) {
  font-size: 1rem;
}

.quality-card .status-tag {
  margin-left: auto;
}

.checked-at {
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
  font-weight: 400;
}

.quality-loading {
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 1rem;
  color: var(--p-text-muted-color);
}

.quality-prompt {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 1rem;
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
}

.quality-content {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.quality-summary {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 1rem;
}

.quality-stat {
  text-align: center;
  padding: 0.75rem;
  background: var(--p-surface-100);
  border-radius: 6px;
}

.quality-stat.has-issues {
  background: var(--p-red-50);
}

.quality-label {
  display: block;
  font-size: 0.75rem;
  color: var(--p-text-muted-color);
  margin-bottom: 0.25rem;
}

.quality-value {
  display: block;
  font-size: 1.25rem;
  font-weight: 600;
}

.quality-stat.has-issues .quality-value {
  color: var(--p-red-500);
}

.quality-of-total {
  font-size: 0.75rem;
  font-weight: 400;
  color: var(--p-text-muted-color);
}

.quality-issues-summary :deep(.p-message) {
  margin: 0;
}

.issues-message {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
}

.quality-healthy {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 1rem;
  background: var(--p-green-50);
  border-radius: 6px;
  color: var(--p-green-700);
}

.quality-healthy i {
  font-size: 1.5rem;
}

.services-warning {
  margin-top: 0.5rem;
}

/* Recent grid — 3 columns */
.recent-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 1rem;
}

.recent-card :deep(.p-card-title) {
  font-size: 0.9375rem;
}

.recent-card :deep(.p-card-content) {
  padding: 0;
}

.recent-card :deep(.p-datatable) {
  border: none;
}

.clickable-rows :deep(.p-datatable-tbody > tr:hover) {
  background-color: var(--p-surface-100);
}

.doc-title {
  font-weight: 500;
}

.doc-id {
  font-size: 0.75rem;
  background: var(--p-surface-100);
  padding: 0.125rem 0.25rem;
  border-radius: 3px;
}

.empty-state {
  text-align: center;
  padding: 1.5rem;
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
}

@media (max-width: 1200px) {
  .recent-grid {
    grid-template-columns: 1fr 1fr;
  }
}

@media (max-width: 768px) {
  .recent-grid {
    grid-template-columns: 1fr;
  }

  .stat-bar {
    gap: 0.5rem;
  }

  .stat-chip {
    min-width: calc(50% - 0.25rem);
  }
}
</style>
